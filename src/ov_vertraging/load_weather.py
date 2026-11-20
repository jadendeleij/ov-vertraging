"""Laad KNMI-uurbestanden in raw.weather_hourly en raw.weather_stations.

Wordt aangeroepen door `python -m ov_vertraging.load`.

Het KNMI-bestand is CSV met een commentaarkop (regels die met '#' beginnen):
- een stationslijst:   "# 260   5.180   52.100   1.90   De Bilt"
- een kolomkop:        "# STN,YYYYMMDD,HH,    T,   RH, ..."
- daarna datarijen met spaties als opvulling; lege velden = niet gemeten.

We lezen de kolomkop per bestand uit (de volgorde komt van KNMI, niet van ons
verzoek) en slaan de kopregels over met `skip`: DuckDB's automatische
CSV-detectie raakt in de war door de lange kop. Per bestand filteren we op de
eigen maand, omdat KNMI bij een einde op uur 24 de volgende dag meelevert.
Waarden blijven in bron-eenheden (vaak tienden).
"""

import logging
import re
from pathlib import Path

import duckdb

from ov_vertraging.config import DB_PATH, WEATHER_DIR

log = logging.getLogger(__name__)

STATION_LINE = re.compile(r"^# (\d{3})\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(.+?)\s*$")
FILE_MONTH = re.compile(r"(\d{4}-\d{2})")


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def parse_header(path: Path) -> tuple[list[str], list[tuple], int]:
    """Lees kolomnamen, stationslijst en aantal kopregels uit een KNMI-bestand."""
    columns: list[str] = []
    stations = []
    n_header = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip() and not line.startswith("#"):
                break  # eerste datarij: kop is voorbij
            n_header += 1
            if line.startswith("# STN,"):
                columns = [c.strip() for c in line[2:].split(",")]
            elif m := STATION_LINE.match(line):
                stn, lon, lat, alt, name = m.groups()
                stations.append((int(stn), float(lon), float(lat), float(alt), name))
    if not columns:
        raise ValueError(f"Geen kolomkop ('# STN,...') gevonden in {path.name}")
    return columns, stations, n_header


def build_file_select(path: Path, columns: list[str], n_header: int) -> str:
    """SELECT voor één bestand: trim, cast naar INTEGER en filter op de eigen maand."""
    month = FILE_MONTH.search(path.name)
    if not month:
        raise ValueError(f"Geen maand (YYYY-MM) in bestandsnaam {path.name}")

    names = [c.lower() for c in columns]
    csv_columns = ", ".join(f"{_sql_str(n)}: 'VARCHAR'" for n in names)
    values = ",\n        ".join(
        f"TRY_CAST(nullif(trim({n}), '') AS INTEGER) AS {n}" for n in names[3:]
    )
    return f"""
    SELECT
        CAST(trim(stn) AS INTEGER) AS station_id,
        strptime(trim(yyyymmdd), '%Y%m%d')::DATE AS date,
        CAST(trim(hh) AS INTEGER) AS hour,
        {values},
        {_sql_str(path.name)} AS source_file
    FROM read_csv(
        {_sql_str(path.as_posix())},
        header = false, skip = {n_header}, auto_detect = false,
        delim = ',', quote = '', columns = {{{csv_columns}}}
    )
    WHERE strftime(strptime(trim(yyyymmdd), '%Y%m%d'), '%Y-%m') = {_sql_str(month.group(1))}
    """


def load_weather(
    db_path: Path = DB_PATH, weather_dir: Path = WEATHER_DIR, pattern: str = "knmi-uur-*.txt"
) -> int:
    """Laad alle KNMI-maandbestanden; geef het aantal uurrijen terug."""
    files = sorted(weather_dir.glob(pattern))
    if not files:
        raise FileNotFoundError(
            f"Geen weerbestanden in {weather_dir}; draai eerst: "
            "python -m ov_vertraging.download_weather --start YYYY-MM --end YYYY-MM"
        )

    selects = []
    stations: dict[int, tuple] = {}
    for path in files:  # gesorteerd: nieuwste bestand wint bij stationsgegevens
        columns, file_stations, n_header = parse_header(path)
        selects.append(build_file_select(path, columns, n_header))
        stations.update({s[0]: s for s in file_stations})

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        union = "\nUNION ALL BY NAME\n".join(selects)
        con.execute(f"CREATE OR REPLACE TABLE raw.weather_hourly AS\n{union}")
        con.execute(
            """
            CREATE OR REPLACE TABLE raw.weather_stations (
                station_id INTEGER, lon DOUBLE, lat DOUBLE, altitude_m DOUBLE, name VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO raw.weather_stations VALUES (?, ?, ?, ?, ?)", sorted(stations.values())
        )
        n_rows = con.execute("SELECT count(*) FROM raw.weather_hourly").fetchone()[0]
    log.info("%d weerbestanden, %d uurrijen, %d weerstations", len(files), n_rows, len(stations))
    return n_rows
