"""SQL voor het dashboard, los van Streamlit zodat het testbaar is.

Alle zware aggregaties gebeuren in DuckDB; er gaan alleen kleine resultaten naar
pandas. Filters worden als parameters (?) meegegeven, nooit in de SQL geplakt.
"""

from dataclasses import dataclass
from datetime import date

import duckdb
import pandas as pd

DOMESTIC_TYPES = ("Intercity", "Intercity direct", "Sprinter", "Stoptrein", "Sneltrein")


@dataclass(frozen=True)
class Filters:
    start: date
    end: date
    service_types: tuple[str, ...]


def where(f: Filters, alias: str = "") -> tuple[str, list]:
    """WHERE-voorwaarden voor fct_departures plus bijbehorende parameters."""
    p = f"{alias}." if alias else ""
    if not f.service_types:
        # Niets geselecteerd = niets tonen (en geen ongeldige "IN ()").
        return "false", []
    placeholders = ", ".join("?" for _ in f.service_types)
    sql = f"{p}departure_date BETWEEN ? AND ? AND {p}service_type IN ({placeholders})"
    return sql, [f.start, f.end, *f.service_types]


def _df(con: duckdb.DuckDBPyConnection, sql: str, params: list) -> pd.DataFrame:
    return con.execute(sql, params).df()


def date_bounds(con: duckdb.DuckDBPyConnection) -> tuple[date, date]:
    return con.execute(
        "SELECT min(departure_date), max(departure_date) FROM marts.fct_departures"
    ).fetchone()


def service_types(con: duckdb.DuckDBPyConnection) -> list[str]:
    rows = con.execute(
        "SELECT service_type FROM marts.fct_departures GROUP BY 1 ORDER BY count(*) DESC"
    ).fetchall()
    return [r[0] for r in rows]


def kpis(con: duckdb.DuckDBPyConnection, f: Filters) -> dict:
    w, params = where(f)
    row = _df(
        con,
        f"""
        SELECT
            count(*) AS vertrekken,
            100 * avg(is_delayed_5min::int) AS pct_vertraagd,
            100 * avg(is_cancelled::int) AS pct_uitval,
            avg(departure_delay_min) FILTER (NOT is_cancelled) AS gem_vertraging
        FROM marts.fct_departures WHERE {w}
        """,
        params,
    ).iloc[0]
    return row.to_dict()


def hour_weekday(con: duckdb.DuckDBPyConnection, f: Filters) -> pd.DataFrame:
    w, params = where(f)
    return _df(
        con,
        f"""
        SELECT departure_weekday AS weekdag, departure_hour AS uur,
               100 * avg(is_delayed_5min::int) AS pct, count(*) AS n
        FROM marts.fct_departures
        WHERE {w} AND is_delayed_5min IS NOT NULL
        GROUP BY ALL ORDER BY ALL
        """,
        params,
    )


def per_day(con: duckdb.DuckDBPyConnection, f: Filters) -> pd.DataFrame:
    w, params = where(f)
    return _df(
        con,
        f"""
        SELECT departure_date AS datum,
               100 * avg(is_delayed_5min::int) AS pct_vertraagd,
               100 * avg(is_cancelled::int) AS pct_uitval,
               count(*) AS vertrekken
        FROM marts.fct_departures WHERE {w}
        GROUP BY 1 HAVING count(*) >= 1000 ORDER BY 1
        """,
        params,
    )


def stations(
    con: duckdb.DuckDBPyConnection,
    f: Filters,
    min_departures: int = 500,
    dutch_only: bool = True,
) -> pd.DataFrame:
    """Punctualiteit per vertrekstation, met coördinaten, voor de kaart."""
    w, params = where(f, "f")
    return _df(
        con,
        f"""
        SELECT s.station_code, s.station_name, s.lat, s.lng,
               count(*) AS vertrekken,
               100 * avg(f.is_delayed_5min::int) AS pct_vertraagd,
               100 * avg(f.is_cancelled::int) AS pct_uitval
        FROM marts.fct_departures AS f
        JOIN marts.dim_station AS s USING (station_code)
        WHERE {w} AND s.lat IS NOT NULL AND (s.is_dutch OR NOT ?)
        GROUP BY ALL HAVING count(*) >= ?
        ORDER BY vertrekken DESC
        """,
        [*params, dutch_only, min_departures],
    )


WEATHER_CONDITIONS = {
    "Normaal (alles)": "true",
    "Windstoten ≥ 9 Bft": "wind_gust_bft >= 9",
    "Wind ≥ 7 Bft (gemiddeld)": "wind_bft >= 7",
    "Vorst (< 0 °C)": "is_frost",
    "Hevige regen (≥ 4 mm/u)": "is_heavy_rain",
    "Sneeuw": "has_snow",
    "Hitte (≥ 30 °C)": "temperature_c >= 30",
}


def weather_effects(con: duckdb.DuckDBPyConnection, f: Filters) -> pd.DataFrame:
    w, params = where(f)
    parts = [
        f"SELECT '{label}' AS weer, count(*) AS vertrekken, "
        f"100 * avg(is_delayed_5min::int) AS pct "
        f"FROM base WHERE {cond}"
        for label, cond in WEATHER_CONDITIONS.items()
    ]
    return _df(
        con,
        f"""
        WITH base AS (
            SELECT * FROM marts.fct_departures WHERE {w} AND is_delayed_5min IS NOT NULL
        )
        {" UNION ALL ".join(parts)}
        ORDER BY pct
        """,
        params,
    )


def temperature_curve(con: duckdb.DuckDBPyConnection, f: Filters) -> pd.DataFrame:
    w, params = where(f)
    return _df(
        con,
        f"""
        SELECT floor(temperature_c / 5) * 5 + 2.5 AS temperatuur,
               100 * avg(is_delayed_5min::int) AS pct, count(*) AS n
        FROM marts.fct_departures
        WHERE {w} AND is_delayed_5min IS NOT NULL AND temperature_c IS NOT NULL
        GROUP BY 1 HAVING count(*) >= 2000 ORDER BY 1
        """,
        params,
    )


def station_options(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Nederlandse stations waar treinen vertrekken, voor de keuzelijsten."""
    return con.execute(
        """
        SELECT s.station_code, s.station_name
        FROM marts.dim_station AS s
        WHERE s.is_dutch AND s.station_code IN (
            SELECT DISTINCT station_code FROM marts.fct_departures
        )
        ORDER BY s.station_name
        """
    ).df()


def _trip_sql(f: Filters) -> tuple[str, list]:
    """Directe ritten van A naar B: dezelfde trein stopt eerst in A en later in B.

    Vertraging bij aankomst in B komt uit staging (arrival_delay_min); fct_departures
    bevat alleen vertrekken.
    """
    if not f.service_types:
        return "SELECT NULL WHERE false", []
    placeholders = ", ".join("?" for _ in f.service_types)
    sql = f"""
        WITH v AS (
            SELECT service_id, service_type, departure_time, departure_time_local,
                   departure_hour, departure_weekday, departure_delay_min, departure_cancelled
            FROM staging.stg_services
            WHERE station_code = ? AND departure_time IS NOT NULL AND is_rail_service
              AND cast(departure_time_local AS date) BETWEEN ? AND ?
              AND service_type IN ({placeholders})
        ),
        a AS (
            SELECT service_id, arrival_time, arrival_delay_min, arrival_cancelled
            FROM staging.stg_services
            WHERE station_code = ? AND arrival_time IS NOT NULL
        )
        SELECT v.service_type, v.departure_time_local AS vertrek, v.departure_hour AS uur,
               v.departure_weekday AS weekdag,
               date_diff('minute', v.departure_time, a.arrival_time) AS reistijd_min,
               v.departure_cancelled OR coalesce(a.arrival_cancelled, false) AS uitgevallen,
               a.arrival_delay_min AS vertraging_aankomst
        FROM v JOIN a ON a.service_id = v.service_id AND a.arrival_time > v.departure_time
    """
    return sql, [f.start, f.end, *f.service_types]


def trip_summary(
    con: duckdb.DuckDBPyConnection, origin: str, destination: str, f: Filters
) -> pd.DataFrame:
    trip, params = _trip_sql(f)
    return _df(
        con,
        f"""
        WITH t AS ({trip})
        SELECT service_type AS treintype, count(*) AS ritten,
               round(median(reistijd_min)) AS reistijd_min,
               100 * avg(uitgevallen::int) AS pct_uitgevallen,
               100 * avg((vertraging_aankomst >= 5)::int) FILTER (NOT uitgevallen)
                   AS pct_aankomst_5min_laat,
               avg(vertraging_aankomst) FILTER (NOT uitgevallen) AS gem_vertraging_aankomst
        FROM t GROUP BY 1 ORDER BY ritten DESC
        """,
        [origin, *params, destination],
    )


def trip_by_hour(
    con: duckdb.DuckDBPyConnection, origin: str, destination: str, f: Filters
) -> pd.DataFrame:
    trip, params = _trip_sql(f)
    return _df(
        con,
        f"""
        WITH t AS ({trip})
        SELECT uur, count(*) AS ritten,
               100 * avg((vertraging_aankomst >= 5)::int) FILTER (NOT uitgevallen)
                   AS pct_aankomst_5min_laat,
               100 * avg(uitgevallen::int) AS pct_uitgevallen
        FROM t GROUP BY 1 ORDER BY 1
        """,
        [origin, *params, destination],
    )
