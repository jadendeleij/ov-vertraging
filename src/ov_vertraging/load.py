"""Laad alle gedownloade maandbestanden in DuckDB-tabel raw.services.

Gebruik:
    python -m ov_vertraging.load

Idempotent: de tabel wordt met CREATE OR REPLACE volledig opnieuw opgebouwd uit
de bestanden in data/raw/services/. DuckDB leest de gzip-CSV's zelf, zodat we de
ruwe rijen nooit in Python-geheugen hoeven te laden.
"""

import logging
import time
from pathlib import Path

import duckdb

from ov_vertraging.config import COLUMN_MAP, DB_PATH, SERVICES_DIR, TIMEZONE, TYPES

log = logging.getLogger(__name__)


def _sql_str(value: str) -> str:
    """Zet een Python-string om naar een veilige SQL-stringliteral."""
    return "'" + value.replace("'", "''") + "'"


def build_load_sql(pattern: str) -> str:
    """Bouw de SQL die alle CSV's in `pattern` inleest met vaste kolomnamen en types."""
    columns = ", ".join(f"{_sql_str(src)}: {_sql_str(dtype)}" for src, dtype in TYPES.items())
    select = ",\n    ".join(f'"{src}" AS {name}' for src, (name, _) in COLUMN_MAP.items())
    return f"""
CREATE OR REPLACE TABLE raw.services AS
SELECT
    {select},
    regexp_extract(filename, '[^/\\\\]+$') AS source_file
FROM read_csv(
    {_sql_str(pattern)},
    header = true,
    columns = {{{columns}}},
    filename = true
)
"""


def load_services(
    db_path: Path = DB_PATH, services_dir: Path = SERVICES_DIR, pattern: str = "*.csv.gz"
) -> int:
    """Laad alle bestanden in services_dir in raw.services en geef het aantal rijen terug."""
    files = sorted(services_dir.glob(pattern))
    if not files:
        raise FileNotFoundError(f"Geen bestanden gevonden in {services_dir} ({pattern})")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with duckdb.connect(str(db_path)) as con:
        con.execute(f"SET TimeZone = {_sql_str(TIMEZONE)}")
        con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        con.execute(build_load_sql((services_dir / pattern).as_posix()))
        n_rows = con.execute("SELECT count(*) FROM raw.services").fetchone()[0]
    log.info(
        "%d bestanden, %d rijen geladen in %.1f s", len(files), n_rows, time.perf_counter() - start
    )
    return n_rows


def summarize(db_path: Path = DB_PATH) -> None:
    """Print een korte samenvatting om de data op logica te controleren."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        con.execute(f"SET TimeZone = {_sql_str(TIMEZONE)}")
        con.sql(
            """
            SELECT
                count(*)                                   AS stops,
                count(DISTINCT service_id)                 AS ritten,
                min(service_date)                          AS eerste_dag,
                max(service_date)                          AS laatste_dag,
                round(100 * avg((departure_delay_min >= 5)::INT), 1) AS pct_vertrek_5min_plus,
                round(100 * avg(service_completely_cancelled::INT), 1) AS pct_stops_rit_geannuleerd
            FROM raw.services
            """
        ).show()
        con.sql(
            """
            SELECT service_type, count(*) AS stops
            FROM raw.services GROUP BY ALL ORDER BY stops DESC LIMIT 10
            """
        ).show()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_services()
    summarize()


if __name__ == "__main__":
    main()
