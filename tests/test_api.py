"""API-tests met TestClient: mini-export, mini-model, nep-weer en nep-NS. Geen internet."""

import json
from datetime import datetime

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from ov_vertraging.model import features as ft
from ov_vertraging.serving.ns import NSClient, parse_departures
from ov_vertraging.serving.predictor import Predictor
from ov_vertraging.serving.weather import WEATHER_FEATURES, to_features

CATS = {
    "service_type": ["Intercity", "Sprinter"],
    "company": ["NS"],
    "station_code": ["BGN", "RSD", "RTB"],
    "next_station_code": ["RSD", "RTB"],
    "route_code": ["BGN-RSD", "RSD-RTB"],
}


class FakeWeather:
    def at(self, lat, lng, day, hour):
        return {**dict.fromkeys(WEATHER_FEATURES, 0), "temperature_c": 5.0, "wind_gust_ms": 8.0}


class FakeNS(NSClient):
    def __init__(self, payload):
        super().__init__(api_key="test")
        self.payload = payload

    def departures_raw(self, station_code):
        return self.payload


NS_PAYLOAD = {
    "payload": {
        "departures": [
            {
                "direction": "Rotterdam Centraal",
                "plannedDateTime": "2026-11-23T17:19:00+0100",
                "actualDateTime": "2026-11-23T17:23:00+0100",
                "plannedTrack": "2",
                "cancelled": False,
                "product": {"number": "2358", "categoryCode": "IC", "operatorName": "NS"},
                "routeStations": [{"uicCode": "8400526", "mediumName": "Roosendaal"}],
            },
            {
                "direction": "Vlissingen",
                "plannedDateTime": "2026-11-23T17:25:00+0100",
                "cancelled": True,
                "product": {"number": "2359", "categoryCode": "IC", "operatorName": "NS"},
                "routeStations": [],
            },
        ]
    }
}


def _write(con, sql: str, path) -> None:
    con.execute(f"COPY ({sql}) TO '{path.as_posix()}' (FORMAT parquet)")


@pytest.fixture
def serving_dir(tmp_path):
    con = duckdb.connect()
    d = tmp_path
    # Maandag (weekday 1): IC 2358 BGN 17:19 -> RSD -> RTB 18:05.
    _write(
        con,
        """SELECT * FROM (VALUES
        (1, 2358, 'Intercity', 'NS', 'BGN', 'RSD', 'BGN-RSD', 1, 1, 1039, NULL),
        (1, 2358, 'Intercity', 'NS', 'RSD', 'RTB', 'RSD-RTB', 2, 1, 1050, 1049),
        (1, 2358, 'Intercity', 'NS', 'RTB', NULL, NULL, 3, 1, NULL, 1085),
        (2, 2360, 'Intercity', 'NS', 'BGN', 'RSD', 'BGN-RSD', 1, 1, 1069, NULL),
        (2, 2360, 'Intercity', 'NS', 'RSD', 'RTB', 'RSD-RTB', 2, 1, 1080, 1079),
        (2, 2360, 'Intercity', 'NS', 'RTB', NULL, NULL, 3, 1, NULL, 1115)
    ) t(service_id, train_number, service_type, company, station_code, next_station_code,
        route_code, stop_sequence, weekday, dep_minute, arr_minute)""",
        d / "timetable.parquet",
    )
    _write(
        con,
        "SELECT 'BGN-RSD' AS route_code, 17 AS departure_hour, 0.07 AS hist_pct, 100 AS hist_n",
        d / "hist_route_hour.parquet",
    )
    _write(
        con,
        "SELECT 'BGN' AS station_code, 0.04 AS hist_pct, 1000 AS hist_n",
        d / "hist_station.parquet",
    )
    _write(
        con,
        "SELECT 2358 AS train_number, 0.07 AS hist_pct, 300 AS hist_n",
        d / "hist_train.parquet",
    )
    _write(
        con,
        "SELECT 'BGN' AS station_code, 1 AS weekday, 17 AS hour, 6.0 AS planned_station_hour",
        d / "planned.parquet",
    )
    _write(
        con,
        "SELECT 1 AS weekday, 17 AS hour, 2400.0 AS planned_network_hour",
        d / "network.parquet",
    )
    _write(
        con,
        """SELECT * FROM (VALUES
        ('BGN', 'Bergen op Zoom', 8400106, 51.49, 4.29, true),
        ('RSD', 'Roosendaal', 8400526, 51.54, 4.46, true),
        ('RTB', 'Rotterdam Blaak', 8400533, 51.92, 4.49, true)
    ) t(station_code, station_name, uic_code, lat, lng, is_dutch)""",
        d / "stations.parquet",
    )
    _write(
        con,
        "SELECT 'zuid' AS region, DATE '2026-10-17' AS start_date, DATE '2026-10-25' AS end_date",
        d / "school_holidays.parquet",
    )
    # Inzichten: september en oktober, Intercity en Sprinter, twee stations.
    _write(
        con,
        """SELECT * FROM (VALUES
        (DATE '2026-09-01', 'Intercity', 'BGN', 1000, 50, 950, 95, 900, 950),
        (DATE '2026-09-01', 'Sprinter',  'RSD', 1000, 20, 980, 30, 500, 980),
        (DATE '2026-10-01', 'Intercity', 'BGN', 1000, 40, 960, 48, 800, 960),
        (DATE '2026-10-01', 'Sprinter',  'RSD', 1000, 10, 990, 20, 400, 990)
    ) t(maand, service_type, station_code, n_all, n_cancelled, n_target, n_delayed,
        sum_delay, n_delay)""",
        d / "insights_station.parquet",
    )
    _write(
        con,
        """SELECT * FROM (VALUES
        (DATE '2026-10-01', 'Intercity', 1, 17, 500, 40),
        (DATE '2026-10-01', 'Sprinter',  1, 8, 500, 10)
    ) t(maand, service_type, weekday, hour, n_target, n_delayed)""",
        d / "insights_hour.parquet",
    )
    _write(
        con,
        """SELECT * FROM (VALUES
        (DATE '2026-10-01', 'Intercity', 'Sneeuw', 1000, 90),
        (DATE '2026-10-01', 'Intercity', 'Gewoon weer', 5000, 200)
    ) t(maand, service_type, weer, n_target, n_delayed)""",
        d / "insights_weather.parquet",
    )

    # Mini-model op willekeurige data met exact de featureset van het echte model.
    rng = np.random.default_rng(0)
    n = 500
    X = pd.DataFrame({c: rng.random(n).astype("float32") for c in ft.NUMERIC})
    for col, cats in CATS.items():
        X[col] = pd.Categorical(rng.choice(cats, n), categories=cats)
    y = (X.hist_route_hour_pct > 0.7).astype(int)
    booster = lgb.train(
        {"objective": "binary", "verbose": -1, "min_data_in_leaf": 5},
        lgb.Dataset(X[ft.FEATURES], label=y, categorical_feature=ft.CATEGORICAL),
        num_boost_round=5,
    )
    booster.save_model(str(d / "model.txt"))
    (d / "model_metadata.json").write_text(json.dumps({"version": "test", "categories": CATS}))
    (d / "manifest.json").write_text(
        json.dumps({"history_until": "2026-11-01", "timetable_days": ["2026-10-26"]})
    )
    return d


@pytest.fixture
def predictor(serving_dir):
    return Predictor(serving_dir, FakeWeather())


@pytest.fixture
def client(predictor):
    with TestClient(create_app(predictor=predictor, ns=NSClient(api_key=None))) as c:
        yield c


# ---------- endpoints ----------


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_version"] == "test"
    assert body["ns_configured"] is False


def test_stations_alleen_met_vertrekken(client):
    names = [s["name"] for s in client.get("/stations").json()]
    assert names == ["Bergen op Zoom", "Roosendaal"]  # RTB is alleen eindpunt


def test_predict_vindt_directe_trein(client):
    resp = client.post(
        "/predict",
        json={
            "from_station": "Bergen op Zoom",
            "to_station": "rtb",
            "departure_time": "2026-11-23T17:00:00",  # maandag
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["train"]["number"] == 2358
    assert body["train"]["planned_departure"].startswith("2026-11-23T17:19")
    assert body["train"]["planned_arrival"].startswith("2026-11-23T18:05")
    assert body["train"]["next_stop"]["code"] == "RSD"
    assert 0 < body["probability"] < 1
    assert body["risk_level"] in {"laag", "gemiddeld", "hoog"}
    assert len(body["factors"]) == 3
    assert body["weather"]["temperature_c"] == 5.0
    # Andere tijden: de gekozen trein en die van 17:49.
    alts = body["alternatives"]
    assert [a["train_number"] for a in alts] == [2358, 2360]
    assert [a["chosen"] for a in alts] == [True, False]
    assert all(0 < a["probability"] < 1 for a in alts)
    # Traject BGN-RSD per uur: alleen 17:00 bekend in de mini-export.
    assert len(body["route_by_hour"]) == 24
    assert body["route_by_hour"][17] == pytest.approx(0.07)
    assert body["route_by_hour"][8] is None
    assert "Rijden de Treinen" in body["attribution"]


def test_predict_onbekend_station_geeft_suggestie(client):
    resp = client.post(
        "/predict",
        json={
            "from_station": "Rotterdam",
            "to_station": "BGN",
            "departure_time": "2026-11-23T17:00",
        },
    )
    assert resp.status_code == 404
    assert "Rotterdam Blaak" in resp.json()["detail"]


def test_predict_zelfde_station(client):
    resp = client.post(
        "/predict",
        json={
            "from_station": "BGN",
            "to_station": "BGN",
            "departure_time": "2026-11-23T17:00",
        },
    )
    assert resp.status_code == 422


def test_predict_verkeerde_richting_geen_trein(client):
    resp = client.post(
        "/predict",
        json={
            "from_station": "RTB",
            "to_station": "BGN",
            "departure_time": "2026-11-23T17:00",
        },
    )
    assert resp.status_code == 404
    assert "Geen directe trein" in resp.json()["detail"]


def test_departures_zonder_key_geeft_503(client):
    resp = client.get("/departures/BGN")
    assert resp.status_code == 503
    assert "NS_API_KEY" in resp.json()["detail"]


def test_departures_met_ns(predictor):
    app = create_app(predictor=predictor, ns=FakeNS(NS_PAYLOAD))
    with TestClient(app) as c:
        body = c.get("/departures/bgn").json()
    first, cancelled = body["departures"]
    assert body["station"]["name"] == "Bergen op Zoom"
    assert first["train_number"] == 2358 and first["type"] == "Intercity"
    assert 0 < first["probability"] < 1
    assert first["current_delay_min"] == 4
    assert first["next_stop"]["name"] == "Roosendaal"
    assert len(first["factors"]) == 3
    assert cancelled["cancelled"] is True and cancelled["probability"] is None


# ---------- onderdelen ----------


def test_parse_departures_koppelt_uic_aan_stationscode():
    deps = parse_departures(NS_PAYLOAD, {"8400526": "RSD"})
    assert deps[0]["next_station_code"] == "RSD"
    assert deps[0]["planned"] == datetime.fromisoformat("2026-11-23T17:19:00+01:00")
    assert deps[1]["next_station_code"] is None


def test_weercodes_naar_features():
    fog = to_features({"weather_code": 45, "snowfall": 0})
    snow = to_features({"weather_code": 3, "snowfall": 0.4})
    ice = to_features({"weather_code": 67})
    assert (fog["has_fog"], fog["has_snow"]) == (1, 0)
    assert snow["has_snow"] == 1
    assert ice["has_ice"] == 1


def test_onbekend_traject_valt_terug_op_gemiddelde(predictor):
    from ov_vertraging.serving.predictor import Departure

    dep = Departure("RSD", "RTB", "Intercity", "NS", 9999, 2, datetime(2026, 11, 23, 17, 30))
    df = predictor.build_features([dep])
    assert df.hist_route_hour_pct.iloc[0] == pytest.approx(predictor.global_rate)
    assert df.hist_train_n.iloc[0] == 0


def test_schoolvakantie_en_feestdag(predictor):
    from ov_vertraging.serving.predictor import Departure

    in_vakantie = Departure("BGN", "RSD", "Intercity", "NS", 2358, 1, datetime(2026, 10, 20, 9))
    kerst = Departure("BGN", "RSD", "Intercity", "NS", 2358, 1, datetime(2026, 12, 25, 9))
    df = predictor.build_features([in_vakantie, kerst])
    assert df.n_regions_school_holiday.tolist() == [1, 0]
    assert df.is_public_holiday.tolist() == [0, 1]


def test_insights_met_filters(client):
    alles = client.get("/insights", params={"months": 1}).json()
    assert alles["period"]["from"] == "2026-10-01"
    assert alles["period"]["compared_to"] == ["2026-09-01", "2026-09-01"]
    assert alles["kpis"]["departures"] == 2000
    # punctualiteit = 1 - 68/1950; vorige maand 1 - 125/1930
    assert alles["kpis"]["punctuality"] == pytest.approx(1 - 68 / 1950)
    assert alles["kpis"]["delta"]["punctuality"] == pytest.approx(
        (1 - 68 / 1950) - (1 - 125 / 1930)
    )
    assert {s["code"] for s in alles["stations"]} == {"BGN", "RSD"}
    assert alles["service_types"] == ["Intercity", "Sprinter"]

    ic = client.get("/insights", params={"months": 2, "type": "Intercity"}).json()
    assert ic["kpis"]["departures"] == 2000
    assert ic["period"]["compared_to"] is None
    assert [h["hour"] for h in ic["heatmap"]] == [17]
    assert ic["weather"][0]["condition"] == "Sneeuw"


def test_website_wordt_geserveerd(predictor, tmp_path, monkeypatch):
    import api.main as main

    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>OV</title>")
    monkeypatch.setattr(main, "WEB_DIR", web)
    with TestClient(main.create_app(predictor=predictor, ns=NSClient(api_key=None))) as c:
        assert "<title>OV</title>" in c.get("/").text
        assert c.get("/health").json()["status"] == "ok"  # API-routes gaan voor
