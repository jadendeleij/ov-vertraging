"""Maak een kleine, echte voorbeelddataset voor CI uit de volledige data.

Gebruik (lokaal, met de volledige data aanwezig):
    python -m ov_vertraging.sample

Schrijft naar tests/fixtures/sample/ in exact het formaat van de bronnen:
    services/services-YYYY-MM.csv.gz   alle ritten langs een paar stations op een paar dagen
    stations/stations-2023-09.csv      het volledige stationsbestand (klein)
    weather/knmi-uur-YYYY-MM.txt       KNMI-kop + uurrijen van de benodigde weerstations

CI draait daarop de echte pipeline:
    OV_RAW_DIR=tests/fixtures/sample OV_DUCKDB_PATH=ci.duckdb python -m ov_vertraging.load
    cd transform && OV_DUCKDB_PATH=../ci.duckdb dbt build

De dagen zijn zo gekozen dat ook de eigen dbt-tests iets te controleren hebben
(wintertijdwissel, feestdagen, schoolvakanties).
"""

import logging
import shutil
from datetime import date
from pathlib import Path

import duckdb

from ov_vertraging.config import (
    COLUMN_MAP,
    DB_PATH,
    PROJECT_ROOT,
    STATIONS_DIR,
    STATIONS_FILENAME,
    WEATHER_DIR,
)

log = logging.getLogger(__name__)

OUT_DIR = PROJECT_ROOT / "tests" / "fixtures" / "sample"
# Kerst, gewone dinsdag, Koningsdag, herfstvakantie noord, herfstvakantie midden/zuid,
# wintertijdwissel (zie transform/tests/assert_*).
DAYS = [
    date(2025, 12, 25),
    date(2026, 3, 10),
    date(2026, 4, 27),
    date(2026, 10, 13),
    date(2026, 10, 21),
    date(2026, 10, 25),
]
# Intercity Vlissingen–Zwolle (Bergen op Zoom) en Sprinters rond 's-Hertogenbosch Oost.
STATIONS = ["BGN", "HTO"]
DE_BILT = 260  # nodig voor de wintertijdtest


def _services(con: duckdb.DuckDBPyConnection, out: Path) -> int:
    days = ", ".join(f"DATE '{d}'" for d in DAYS)
    stations = ", ".join(f"'{s}'" for s in STATIONS)
    select = ", ".join(f'{name} AS "{src}"' for src, (name, _) in COLUMN_MAP.items())
    con.execute(
        f"""
        CREATE TEMP TABLE sample_services AS
        SELECT * FROM raw.services
        WHERE service_id IN (
            SELECT service_id FROM raw.services
            WHERE service_date IN ({days}) AND station_code IN ({stations})
        )
        """
    )
    months = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT strftime(service_date, '%Y-%m') FROM sample_services ORDER BY 1"
        ).fetchall()
    ]
    (out / "services").mkdir(parents=True)
    for month in months:
        path = out / "services" / f"services-{month}.csv.gz"
        con.execute(
            f"""
            COPY (
                SELECT {select} FROM sample_services
                WHERE strftime(service_date, '%Y-%m') = '{month}'
                ORDER BY service_id, stop_id
            ) TO '{path.as_posix()}' (HEADER, DELIMITER ',', COMPRESSION gzip,
                TIMESTAMPFORMAT '%Y-%m-%dT%H:%M:%S%z')
            """
        )
    return con.execute("SELECT count(*) FROM sample_services").fetchone()[0]


def _weather_stations(con: duckdb.DuckDBPyConnection) -> set[int]:
    """Weerstations die de sample-stations (via int_station_weather_map) gebruiken."""
    rows = con.execute(
        """
        SELECT temperature_station_id, precipitation_station_id, wind_station_id,
               visibility_station_id
        FROM intermediate.int_station_weather_map
        WHERE station_code IN (SELECT DISTINCT station_code FROM sample_services)
        """
    ).fetchall()
    return {int(v) for row in rows for v in row if v is not None} | {DE_BILT}


def _weather(stations: set[int], out: Path) -> int:
    (out / "weather").mkdir(parents=True)
    wanted_days = {d.strftime("%Y%m%d") for d in DAYS}
    n = 0
    for month in sorted({d.strftime("%Y-%m") for d in DAYS}):
        src = WEATHER_DIR / f"knmi-uur-{month}.txt"
        lines = []
        for line in src.read_text(encoding="utf-8").splitlines(keepends=True):
            if line.startswith("#") or not line.strip():
                lines.append(line)  # kop blijft volledig: kolommen + stationslijst
                continue
            stn, day = line.split(",", 2)[:2]
            if int(stn) in stations and day.strip() in wanted_days:
                lines.append(line)
                n += 1
        (out / "weather" / src.name).write_text("".join(lines), encoding="utf-8")
    return n


def build(db_path: Path = DB_PATH, out: Path = OUT_DIR) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with duckdb.connect(str(db_path), read_only=True) as con:
        n_services = _services(con, out)
        weather_stations = _weather_stations(con)
    (out / "stations").mkdir()
    shutil.copy(STATIONS_DIR / STATIONS_FILENAME, out / "stations" / STATIONS_FILENAME)
    n_weather = _weather(weather_stations, out)
    size_kb = sum(p.stat().st_size for p in out.rglob("*") if p.is_file()) / 1e3
    info = {
        "stops": n_services,
        "weather_rows": n_weather,
        "weather_stations": sorted(weather_stations),
        "size_kb": round(size_kb),
    }
    log.info("Voorbeelddataset: %s", info)
    return info


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build()


if __name__ == "__main__":
    main()
