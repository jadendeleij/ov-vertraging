"""Featureset voor het model, gebouwd in DuckDB.

Gebruik (wordt ook door train.py aangeroepen):
    python -m ov_vertraging.model.features

Maakt de tabel ml.departure_features: één rij per binnenlands treinvertrek met een
target, met alleen features die een paar uur vóór vertrek bekend zijn. De
historische features gebruiken uitsluitend dagen vóór de vertrekdag (zie
_history_sql). Welke kolommen verboden zijn staat in FORBIDDEN en wordt getest.
"""

import logging
from dataclasses import dataclass
from datetime import date

import duckdb
import pandas as pd

from ov_vertraging.config import DB_PATH

log = logging.getLogger(__name__)

TABLE = "ml.departure_features"
TARGET = "is_delayed_5min"
DOMESTIC_TYPES = ("Intercity", "Intercity direct", "Sprinter", "Stoptrein", "Sneltrein")

# Smoothing van historische percentages: een groep met weinig waarnemingen wordt naar
# het (eveneens historische) landelijke gemiddelde getrokken. 50 = "telt als 50
# vertrekken met het landelijke gemiddelde".
PRIOR_WEIGHT = 50

CATEGORICAL = [
    "service_type",
    "company",
    "station_code",
    "next_station_code",
    "route_code",
]
NUMERIC = [
    # tijd en kalender
    "departure_hour",
    "departure_weekday",
    "month",
    "is_weekend",
    "is_public_holiday",
    "n_regions_school_holiday",
    # rit en drukte (dienstregeling)
    "stop_sequence",
    "is_first_stop",
    "planned_station_hour",
    "planned_network_hour",
    # weer (in productie: verwachting)
    "temperature_c",
    "precipitation_mm",
    "wind_speed_ms",
    "wind_gust_ms",
    "visibility_m",
    "humidity_pct",
    "has_snow",
    "has_fog",
    "has_ice",
    # geschiedenis t/m gisteren
    "hist_route_hour_pct",
    "hist_route_hour_n",
    "hist_station_pct",
    "hist_train_pct",
    "hist_train_n",
]
FEATURES = CATEGORICAL + NUMERIC

# Informatie die op het voorspelmoment niet bekend is (zie docs/model.md §3).
FORBIDDEN = {
    "departure_delay_min",
    "arrival_delay_min",
    "is_cancelled",
    "service_completely_cancelled",
    "service_partly_cancelled",
    "service_max_delay_min",
    "platform_change",
    "has_invalid_delay",
    "is_delayed_5min",
    "pct_delayed_5min",  # agg_route_hour: over de hele periode = toekomst
    "avg_delay_min",
    "p90_delay_min",
}


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # inclusief


SPLITS = {
    "train": Period(date(2025, 11, 1), date(2026, 7, 31)),
    "validation": Period(date(2026, 8, 1), date(2026, 8, 31)),
    "test": Period(date(2026, 9, 1), date(2026, 10, 31)),
}


def _history_sql(key_cols: list[str], name: str) -> str:
    """Historisch % vertraagd per groep, alleen over dagen vóór de vertrekdag.

    Eerst één rij per groep per dag; daarna een lopend totaal over de rijen ervóór
    (ROWS ... 1 PRECEDING), dus nooit de dag zelf of later.
    """
    keys = ", ".join(key_cols)
    return f"""
    {name}_daily AS (
        SELECT {keys}, departure_date,
               count(*) AS n, count(*) FILTER (is_delayed_5min) AS n_delayed
        FROM base GROUP BY ALL
    ),
    {name} AS (
        SELECT {keys}, departure_date,
               coalesce(sum(n) OVER w, 0) AS hist_n,
               coalesce(sum(n_delayed) OVER w, 0) AS hist_delayed
        FROM {name}_daily
        WINDOW w AS (
            PARTITION BY {keys} ORDER BY departure_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        )
    )"""


def build_sql(prior_weight: int = PRIOR_WEIGHT) -> str:
    domestic = ", ".join(f"'{t}'" for t in DOMESTIC_TYPES)
    smoothed = (  # (historisch vertraagd + prior) / (historisch aantal + prior-gewicht)
        "({h}.hist_delayed + {w} * g.hist_rate) / ({h}.hist_n + {w})"
    )
    return f"""
    CREATE SCHEMA IF NOT EXISTS ml;
    CREATE OR REPLACE TABLE {TABLE} AS
    WITH base AS (
        SELECT f.*
        FROM marts.fct_departures AS f
        JOIN marts.dim_station AS s ON s.station_code = f.station_code AND s.is_dutch
        WHERE f.service_type IN ({domestic}) AND f.is_delayed_5min IS NOT NULL
    ),
    -- Drukte uit de dienstregeling: alle geplande vertrekken, ook als ze later uitvielen.
    planned_station AS (
        SELECT station_code, departure_date, departure_hour, count(*) AS planned_station_hour
        FROM marts.fct_departures GROUP BY ALL
    ),
    planned_network AS (
        SELECT departure_date, departure_hour, count(*) AS planned_network_hour
        FROM marts.fct_departures GROUP BY ALL
    ),
    -- Landelijk historisch percentage t/m gisteren, als prior voor de smoothing.
    global_daily AS (
        SELECT departure_date, count(*) AS n, count(*) FILTER (is_delayed_5min) AS n_delayed
        FROM base GROUP BY 1
    ),
    g AS (
        SELECT departure_date,
               coalesce(
                   sum(n_delayed) OVER w / nullif(sum(n) OVER w, 0),
                   0.04  -- allereerste dag: nog geen geschiedenis
               ) AS hist_rate
        FROM global_daily
        WINDOW w AS (ORDER BY departure_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
    ),
    {_history_sql(["route_code", "departure_hour"], "h_route")},
    {_history_sql(["station_code"], "h_station")},
    {_history_sql(["train_number"], "h_train")}
    SELECT
        b.departure_id,
        b.departure_date,
        b.{TARGET},
        b.service_type,
        b.company,
        b.station_code,
        b.next_station_code,
        b.route_code,
        b.departure_hour,
        b.departure_weekday,
        month(b.departure_date) AS month,
        (b.departure_weekday >= 6)::int AS is_weekend,
        d.is_public_holiday::int AS is_public_holiday,
        d.n_regions_school_holiday,
        b.stop_sequence,
        b.is_first_stop::int AS is_first_stop,
        ps.planned_station_hour,
        pn.planned_network_hour,
        b.temperature_c,
        b.precipitation_mm,
        b.wind_speed_ms,
        b.wind_gust_ms,
        b.visibility_m,
        b.humidity_pct,
        b.has_snow::int AS has_snow,
        b.has_fog::int AS has_fog,
        b.has_ice::int AS has_ice,
        {smoothed.format(h="hr", w=prior_weight)} AS hist_route_hour_pct,
        hr.hist_n AS hist_route_hour_n,
        {smoothed.format(h="hs", w=prior_weight)} AS hist_station_pct,
        {smoothed.format(h="ht", w=prior_weight)} AS hist_train_pct,
        ht.hist_n AS hist_train_n
    FROM base AS b
    JOIN marts.dim_date AS d ON d.date_day = b.departure_date
    JOIN g USING (departure_date)
    JOIN planned_station AS ps USING (station_code, departure_date, departure_hour)
    JOIN planned_network AS pn USING (departure_date, departure_hour)
    JOIN h_route AS hr USING (route_code, departure_hour, departure_date)
    JOIN h_station AS hs USING (station_code, departure_date)
    JOIN h_train AS ht USING (train_number, departure_date)
    """


def build_feature_table(con: duckdb.DuckDBPyConnection, prior_weight: int = PRIOR_WEIGHT) -> int:
    """Maak ml.departure_features opnieuw (idempotent); geef het aantal rijen terug."""
    con.execute(build_sql(prior_weight))
    n = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
    log.info("%s: %d rijen, %d features", TABLE, n, len(FEATURES))
    return n


def load_split(
    con: duckdb.DuckDBPyConnection,
    split: str,
    sample_n: int | None = None,
    seed: int = 42,
) -> pd.DataFrame:
    """Laad één split als pandas DataFrame (categorisch als 'category', rest float32).

    sample_n: willekeurige steekproef binnen de periode (reproduceerbaar via seed);
    nodig om binnen 8 GB werkgeheugen te blijven.
    """
    period = SPLITS[split]
    cols = ", ".join(["departure_id", "departure_date", TARGET, *FEATURES])
    sample = (
        f"USING SAMPLE reservoir({int(sample_n)} ROWS) REPEATABLE ({int(seed)})" if sample_n else ""
    )
    sql = f"""
        SELECT {cols} FROM {TABLE}
        WHERE departure_date BETWEEN ? AND ?
        {sample}
    """
    df = con.execute(sql, [period.start, period.end]).df()
    for col in CATEGORICAL:
        df[col] = df[col].astype("category")
    df[NUMERIC] = df[NUMERIC].astype("float32")
    df[TARGET] = df[TARGET].astype("int8")
    return df


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(DB_PATH)) as con:
        build_feature_table(con)


if __name__ == "__main__":
    main()
