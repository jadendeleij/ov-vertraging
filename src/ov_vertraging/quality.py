"""Datakwaliteitschecks op raw.services.

Elke check geeft een CheckResult terug. Een mislukte check betekent dat de data
niet te vertrouwen is; load.py stopt dan met een DataQualityError. Alle checks
zijn SQL in DuckDB, zodat ze ook op miljoenen rijen snel blijven.

Gebruik los:
    python -m ov_vertraging.quality
"""

import logging
from dataclasses import dataclass

import duckdb

from ov_vertraging.config import DB_PATH

log = logging.getLogger(__name__)

TABLE = "raw.services"
KEY_COLUMNS = ["service_id", "stop_id", "service_date", "station_code"]

# Vertraging buiten dit bereik is vrijwel zeker een datafout (bv. de DB-treinen
# met precies 1440 min = 24 uur). Een klein aandeel tolereren we; dat filteren
# we in de staging-laag weg.
DELAY_MIN, DELAY_MAX = -5, 600
MAX_OUT_OF_RANGE_FRACTION = 0.001  # 0,1 %


class DataQualityError(Exception):
    """Een of meer datakwaliteitschecks zijn mislukt."""


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def _scalar(con: duckdb.DuckDBPyConnection, sql: str):
    return con.execute(sql).fetchone()[0]


def check_row_count(con: duckdb.DuckDBPyConnection) -> CheckResult:
    n = _scalar(con, f"SELECT count(*) FROM {TABLE}")
    return CheckResult("aantal rijen > 0", n > 0, f"{n:,} rijen")


def check_unique_stop_id(con: duckdb.DuckDBPyConnection) -> CheckResult:
    dups = _scalar(con, f"SELECT count(*) - count(DISTINCT stop_id) FROM {TABLE}")
    return CheckResult("stop_id uniek", dups == 0, f"{dups:,} duplicaten")


def check_keys_not_null(con: duckdb.DuckDBPyConnection) -> CheckResult:
    parts = ", ".join(f"count(*) FILTER ({c} IS NULL) AS {c}" for c in KEY_COLUMNS)
    row = con.execute(f"SELECT {parts} FROM {TABLE}").fetchone()
    nulls = {c: n for c, n in zip(KEY_COLUMNS, row, strict=True) if n}
    detail = "geen NULLs" if not nulls else ", ".join(f"{c}: {n:,}" for c, n in nulls.items())
    return CheckResult("sleutelkolommen niet NULL", not nulls, detail)


def check_min_two_stops(con: duckdb.DuckDBPyConnection) -> CheckResult:
    bad = _scalar(
        con,
        f"SELECT count(*) FROM (SELECT service_id FROM {TABLE} GROUP BY 1 HAVING count(*) < 2)",
    )
    return CheckResult("elke rit ≥ 2 stops", bad == 0, f"{bad:,} ritten met 1 stop")


def check_delay_range(con: duckdb.DuckDBPyConnection) -> CheckResult:
    n_out, n_total = con.execute(
        f"""
        SELECT
            count(*) FILTER (departure_delay_min NOT BETWEEN {DELAY_MIN} AND {DELAY_MAX}),
            count(departure_delay_min)
        FROM {TABLE}
        """
    ).fetchone()
    fraction = n_out / n_total if n_total else 0.0
    return CheckResult(
        f"departure_delay_min in [{DELAY_MIN}, {DELAY_MAX}]",
        fraction <= MAX_OUT_OF_RANGE_FRACTION,
        f"{n_out:,} buiten bereik ({fraction:.4%}, max {MAX_OUT_OF_RANGE_FRACTION:.1%})",
    )


CHECKS = [
    check_row_count,
    check_unique_stop_id,
    check_keys_not_null,
    check_min_two_stops,
    check_delay_range,
]


def null_percentages(con: duckdb.DuckDBPyConnection) -> dict[str, float]:
    """Percentage NULL per kolom, in één scan over de tabel."""
    columns = [r[0] for r in con.execute(f"DESCRIBE {TABLE}").fetchall()]
    # coalesce: op een lege tabel geeft avg() NULL; dan rapporteren we 0 %.
    parts = ", ".join(f'coalesce(100.0 * avg(("{c}" IS NULL)::INT), 0)' for c in columns)
    row = con.execute(f"SELECT {parts} FROM {TABLE}").fetchone()
    return dict(zip(columns, row, strict=True))


def run_checks(con: duckdb.DuckDBPyConnection) -> list[CheckResult]:
    """Draai alle checks, log de uitkomsten en het NULL-percentage per kolom."""
    results = [check(con) for check in CHECKS]
    for r in results:
        log.log(
            logging.INFO if r.passed else logging.ERROR,
            "%s %s — %s",
            "OK  " if r.passed else "FOUT",
            r.name,
            r.detail,
        )
    for column, pct in null_percentages(con).items():
        if pct > 0:
            log.info("NULL %-30s %5.1f%%", column, pct)
    return results


def assert_quality(con: duckdb.DuckDBPyConnection) -> None:
    """Gooi een DataQualityError als er een check mislukt."""
    failed = [r for r in run_checks(con) if not r.passed]
    if failed:
        raise DataQualityError("; ".join(f"{r.name}: {r.detail}" for r in failed))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        assert_quality(con)


if __name__ == "__main__":
    main()
