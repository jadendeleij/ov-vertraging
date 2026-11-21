"""Voorspellen in de live app: trein zoeken, features bouwen, kans + uitleg.

Werkt alleen op de slanke export in models/serving/ (zie export.py), niet op de grote
database. De features worden precies zo opgebouwd als in model/features.py; het
enige verschil is dat het weer hier een verwachting is in plaats van een meting.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from ov_vertraging.calendar_seeds import nl_public_holidays
from ov_vertraging.model import features as ft
from ov_vertraging.serving.weather import WEATHER_FEATURES

TZ = ZoneInfo("Europe/Amsterdam")
TABLES = [
    "timetable",
    "hist_route_hour",
    "hist_station",
    "hist_train",
    "planned",
    "network",
    "stations",
    "school_holidays",
    "insights_station",
    "insights_hour",
    "insights_weather",
]


@dataclass(frozen=True)
class Departure:
    """Eén vertrek waarvoor we een kans willen: wat de dienstregeling erover zegt."""

    station_code: str
    next_station_code: str | None
    service_type: str | None
    company: str | None
    train_number: int | None
    stop_sequence: int | None
    planned: datetime  # Nederlandse tijd


def _local(dt: datetime) -> datetime:
    """Tijd zonder zone = Nederlandse tijd; met zone = omrekenen."""
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


# ---------- uitleg ----------

LABELS = {
    "hist_route_hour_pct": lambda v: (
        f"dit traject op dit uur was eerder {v:.0%} van de keren vertraagd"
    ),
    "hist_train_pct": lambda v: f"dit treinnummer was eerder {v:.0%} van de keren vertraagd",
    "hist_station_pct": lambda v: f"vertrekken vanaf dit station waren eerder {v:.0%} vertraagd",
    "hist_train_n": lambda v: f"over dit treinnummer zijn {v:.0f} eerdere ritten bekend",
    "hist_route_hour_n": lambda v: (
        f"over dit traject op dit uur zijn {v:.0f} eerdere ritten bekend"
    ),
    "planned_network_hour": lambda v: (
        f"drukte op het spoor: ~{v:.0f} geplande vertrekken landelijk in dit uur"
    ),
    "planned_station_hour": lambda v: f"~{v:.0f} geplande vertrekken op dit station in dit uur",
    "departure_hour": lambda v: f"vertrek rond {v:.0f}:00",
    "departure_weekday": lambda v: (
        "dag van de week: " + ["ma", "di", "wo", "do", "vr", "za", "zo"][int(v) - 1]
    ),
    "month": lambda v: f"maand {v:.0f}",
    "stop_sequence": lambda v: f"dit is stop nr. {v:.0f} van de rit",
    "is_first_stop": lambda v: "de trein begint hier" if v else "de trein rijdt al",
    "temperature_c": lambda v: f"verwachte temperatuur {v:.1f} °C",
    "precipitation_mm": lambda v: f"verwachte neerslag {v:.1f} mm",
    "wind_speed_ms": lambda v: f"verwachte wind {v:.0f} m/s",
    "wind_gust_ms": lambda v: f"verwachte windstoten {v:.0f} m/s",
    "visibility_m": lambda v: f"verwacht zicht {v / 1000:.1f} km",
    "humidity_pct": lambda v: f"verwachte luchtvochtigheid {v:.0f} %",
    "has_snow": lambda v: "sneeuw verwacht" if v else "geen sneeuw verwacht",
    "has_fog": lambda v: "mist verwacht" if v else "geen mist verwacht",
    "has_ice": lambda v: "ijzel verwacht" if v else "geen ijzel verwacht",
    "is_public_holiday": lambda v: "feestdag" if v else "geen feestdag",
    "n_regions_school_holiday": lambda v: f"schoolvakantie in {v:.0f} van de 3 regio's",
    "is_weekend": lambda v: "weekend" if v else "doordeweeks",
    "route_code": lambda v: f"traject {v}",
    "station_code": lambda v: f"station {v}",
    "next_station_code": lambda v: f"volgende stop {v}",
    "service_type": lambda v: f"treintype {v}",
    "company": lambda v: f"vervoerder {v}",
}


def describe(feature: str, value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return f"{feature}: onbekend"
    try:
        return LABELS[feature](value)
    except (KeyError, ValueError, TypeError, IndexError):
        return f"{feature} = {value}"


# ---------- predictor ----------


class Predictor:
    def __init__(self, serving_dir: Path, weather):
        self.dir = Path(serving_dir)
        self.weather = weather
        self.meta = json.loads((self.dir / "model_metadata.json").read_text())
        self.manifest = json.loads((self.dir / "manifest.json").read_text())
        self.booster = lgb.Booster(model_file=str(self.dir / "model.txt"))
        # In het geheugen; de bestanden zijn klein. Elke query gebruikt een eigen cursor
        # (self.con.cursor()): de API beantwoordt verzoeken in meerdere threads tegelijk.
        self.con = duckdb.connect()
        for name in TABLES:
            path = (self.dir / f"{name}.parquet").as_posix()
            self.con.cursor().execute(
                f"CREATE TABLE {name} AS SELECT * FROM read_parquet('{path}')"
            )
        self.stations = (
            self.con.cursor().execute("SELECT * FROM stations").df().set_index("station_code")
        )
        self.global_rate = (
            self.con.cursor()
            .execute("SELECT sum(hist_pct * hist_n) / sum(hist_n) FROM hist_station")
            .fetchone()[0]
        )

    # --- stations ---

    def resolve_station(self, text: str) -> str | None:
        """Stationscode bij een code ('ut') of naam ('Utrecht Centraal')."""
        dutch = self.stations[self.stations.is_dutch.fillna(False)]
        code = text.strip().upper()
        if code in dutch.index:
            return code
        by_name = dutch.index[dutch.station_name.str.casefold() == text.strip().casefold()]
        return by_name[0] if len(by_name) else None

    def station_list(self) -> list[dict]:
        """Nederlandse stations waar in de dienstregeling treinen vertrekken."""
        codes = {
            r[0]
            for r in self.con.cursor()
            .execute("SELECT DISTINCT station_code FROM timetable WHERE dep_minute IS NOT NULL")
            .fetchall()
        }
        dutch = self.stations[self.stations.is_dutch.fillna(False)]
        rows = dutch[dutch.index.isin(codes)].station_name.sort_values()
        return [{"code": code, "name": name} for code, name in rows.items()]

    def suggest_stations(self, text: str, limit: int = 5) -> list[str]:
        dutch = self.stations[self.stations.is_dutch.fillna(False)]
        mask = dutch.station_name.str.contains(text.strip(), case=False, regex=False)
        return dutch.station_name[mask].sort_values().head(limit).tolist()

    def station_name(self, code: str | None) -> str | None:
        return self.stations.station_name.get(code) if code else None

    # --- dienstregeling ---

    TRIP_COLS = [
        "service_id",
        "train_number",
        "service_type",
        "company",
        "station_code",
        "next_station_code",
        "stop_sequence",
        "dep_minute",
        "arr_minute",
    ]

    def find_trips(
        self, origin: str, destination: str, when: datetime, before: int = 1, after: int = 2
    ) -> tuple[list[dict], int]:
        """Directe treinen van A naar B rond `when`, volgens de dienstregeling van
        dezelfde weekdag in de laatste week van de data.

        Geeft (ritten op volgorde van vertrek, index van de gekozen rit): de eerste rit
        vanaf ~het gevraagde tijdstip, met `before` ritten ervoor en `after` erna.
        """
        when = _local(when)
        minute = when.hour * 60 + when.minute
        rows = (
            self.con.cursor()
            .execute(
                """
            SELECT DISTINCT ON (a.dep_minute)
                   a.service_id, a.train_number, a.service_type, a.company,
                   a.station_code, a.next_station_code, a.stop_sequence,
                   a.dep_minute, coalesce(b.arr_minute, b.dep_minute) AS arr_minute
            FROM timetable AS a
            JOIN timetable AS b
              ON b.service_id = a.service_id AND b.stop_sequence > a.stop_sequence
            WHERE a.station_code = ? AND b.station_code = ? AND a.weekday = ?
              AND a.dep_minute BETWEEN ? - 120 AND ? + 240
            ORDER BY a.dep_minute, coalesce(b.arr_minute, b.dep_minute)
            """,
                [origin, destination, when.isoweekday(), minute, minute],
            )
            .fetchall()
        )
        trips = [self._trip(dict(zip(self.TRIP_COLS, r, strict=True)), when) for r in rows]
        if not trips:
            return [], -1
        later = [i for i, t in enumerate(trips) if t["dep_minute"] >= minute - 5]
        chosen = later[0] if later else len(trips) - 1
        lo = max(0, chosen - before)
        return trips[lo : chosen + after + 1], chosen - lo

    def find_trip(self, origin: str, destination: str, when: datetime) -> dict | None:
        """De eerste directe trein van A naar B vanaf ~`when`."""
        trips, chosen = self.find_trips(origin, destination, when, before=0, after=0)
        return trips[chosen] if trips else None

    @staticmethod
    def _trip(trip: dict, when: datetime) -> dict:
        trip["planned_departure"] = when.replace(
            hour=trip["dep_minute"] // 60,
            minute=trip["dep_minute"] % 60,
            second=0,
            microsecond=0,
        )
        if trip["arr_minute"] is not None:
            trip["planned_arrival"] = when.replace(
                hour=(trip["arr_minute"] // 60) % 24,
                minute=trip["arr_minute"] % 60,
                second=0,
                microsecond=0,
            )
        return trip

    def route_hours(self, route_code: str | None) -> list[float | None]:
        """Historisch % vertraagd van dit traject (eerste rit-deel) per uur, 0-23."""
        values: list[float | None] = [None] * 24
        if not route_code:
            return values
        for hour, pct in (
            self.con.cursor()
            .execute(
                "SELECT departure_hour, hist_pct FROM hist_route_hour WHERE route_code = ?",
                [route_code],
            )
            .fetchall()
        ):
            values[int(hour)] = float(pct)
        return values

    def stop_sequence_for(self, train_number: int | None, station: str, weekday: int):
        if train_number is None:
            return None
        row = (
            self.con.cursor()
            .execute(
                "SELECT stop_sequence FROM timetable WHERE train_number = ? AND station_code = ? "
                "ORDER BY (weekday = ?)::int DESC LIMIT 1",
                [train_number, station, weekday],
            )
            .fetchone()
        )
        return row[0] if row else None

    # --- features ---

    def _school_regions(self, day: date) -> int:
        return (
            self.con.cursor()
            .execute(
                "SELECT count(DISTINCT region) FROM school_holidays "
                "WHERE ? BETWEEN start_date AND end_date",
                [day],
            )
            .fetchone()[0]
        )

    def build_features(self, departures: list[Departure]) -> pd.DataFrame:
        rows = []
        for d in departures:
            t = _local(d.planned)
            st = (
                self.stations.loc[d.station_code] if d.station_code in self.stations.index else None
            )
            weather = (
                self.weather.at(float(st.lat), float(st.lng), t.date(), t.hour)
                if st is not None and pd.notna(st.lat)
                else dict.fromkeys(WEATHER_FEATURES)
            )
            holidays = {h for h, _ in nl_public_holidays(t.year)}
            rows.append(
                {
                    "station_code": d.station_code,
                    "next_station_code": d.next_station_code,
                    "route_code": f"{d.station_code}-{d.next_station_code}"
                    if d.next_station_code
                    else None,
                    "service_type": d.service_type,
                    "company": d.company,
                    "train_number": d.train_number,
                    "departure_hour": t.hour,
                    "departure_weekday": t.isoweekday(),
                    "month": t.month,
                    "is_weekend": int(t.isoweekday() >= 6),
                    "is_public_holiday": int(t.date() in holidays),
                    "n_regions_school_holiday": self._school_regions(t.date()),
                    "stop_sequence": d.stop_sequence,
                    "is_first_stop": None if d.stop_sequence is None else int(d.stop_sequence == 1),
                    **weather,
                }
            )
        df = pd.DataFrame(rows)

        def lookup(table: str, keys: list[str], cols: dict[str, str]) -> None:
            nonlocal df
            ref = (
                self.con.cursor()
                .execute(f"SELECT {', '.join(keys + list(cols))} FROM {table}")
                .df()
            )
            df = df.merge(ref.rename(columns=cols), on=keys, how="left")

        lookup(
            "hist_route_hour",
            ["route_code", "departure_hour"],
            {"hist_pct": "hist_route_hour_pct", "hist_n": "hist_route_hour_n"},
        )
        lookup("hist_station", ["station_code"], {"hist_pct": "hist_station_pct"})
        lookup(
            "hist_train", ["train_number"], {"hist_pct": "hist_train_pct", "hist_n": "hist_train_n"}
        )
        df = df.merge(
            self.con.cursor()
            .execute("SELECT * FROM planned")
            .df()
            .rename(columns={"weekday": "departure_weekday", "hour": "departure_hour"}),
            on=["station_code", "departure_weekday", "departure_hour"],
            how="left",
        ).merge(
            self.con.cursor()
            .execute("SELECT * FROM network")
            .df()
            .rename(columns={"weekday": "departure_weekday", "hour": "departure_hour"}),
            on=["departure_weekday", "departure_hour"],
            how="left",
        )
        # Zonder geschiedenis: het landelijke gemiddelde, net als de smoothing in training.
        for col in ["hist_route_hour_pct", "hist_station_pct", "hist_train_pct"]:
            df[col] = df[col].fillna(self.global_rate)
        for col in ["hist_route_hour_n", "hist_train_n"]:
            df[col] = df[col].fillna(0)
        return df

    def _model_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        X = df[ft.FEATURES].copy()
        for col in ft.NUMERIC:
            X[col] = pd.to_numeric(X[col], errors="coerce").astype("float32")
        for col in ft.CATEGORICAL:
            X[col] = X[col].astype("category")
        return ft.align_categories(X, self.meta["categories"])

    # --- voorspellen ---

    @staticmethod
    def risk_level(p: float, average: float) -> str:
        """Laag/gemiddeld/hoog t.o.v. het gemiddelde van het model zelf.

        Bewust niet t.o.v. het historische percentage: het model onderschat licht (zie
        docs/model.md), en de factoren (SHAP) zijn ook t.o.v. het modelgemiddelde. Zo
        kloppen niveau en uitleg met elkaar.
        """
        if p < 0.75 * average:
            return "laag"
        if p < 1.5 * average:
            return "gemiddeld"
        return "hoog"

    def predict(self, departures: list[Departure], top_k: int = 3) -> list[dict]:
        if not departures:
            return []
        df = self.build_features(departures)
        X = self._model_frame(df)
        probs = self.booster.predict(X)
        raw = self.booster.predict(X, pred_contrib=True)
        contrib, base = raw[:, :-1], raw[:, -1]  # laatste kolom = modelgemiddelde (log-odds)
        results = []
        for i, p in enumerate(probs):
            order = np.argsort(-np.abs(contrib[i]))[:top_k]
            average = float(1 / (1 + np.exp(-base[i])))
            results.append(
                {
                    "probability": float(p),
                    "model_average": average,
                    "risk_level": self.risk_level(float(p), average),
                    "factors": [
                        {
                            "feature": ft.FEATURES[j],
                            "description": describe(ft.FEATURES[j], df.iloc[i][ft.FEATURES[j]]),
                            "effect": "verhoogt" if contrib[i, j] > 0 else "verlaagt",
                            "impact": round(float(contrib[i, j]), 3),
                        }
                        for j in order
                    ],
                    "weather": {k: _clean(df.iloc[i][k]) for k in WEATHER_FEATURES},
                }
            )
        return results


def _clean(value):
    """NaN/numpy-typen naar gewone JSON-waarden."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    return value.item() if hasattr(value, "item") else value
