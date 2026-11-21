"""De meegeleverde voorbeelddataset (tests/fixtures/sample) laadt foutloos en haalt alle
kwaliteitschecks. CI draait daarna `dbt build` op dezelfde data."""

from pathlib import Path

import duckdb

from ov_vertraging.load import load_services, load_stations
from ov_vertraging.load_weather import load_weather
from ov_vertraging.quality import ALL_CHECKS, run_checks

SAMPLE = Path(__file__).parent / "fixtures" / "sample"


def test_voorbeelddataset_haalt_alle_checks(tmp_path):
    db = tmp_path / "sample.duckdb"
    assert load_services(db_path=db, services_dir=SAMPLE / "services") > 10_000
    load_stations(db_path=db, stations_file=SAMPLE / "stations" / "stations-2023-09.csv")
    assert load_weather(db_path=db, weather_dir=SAMPLE / "weather") > 1_000
    with duckdb.connect(str(db), read_only=True) as con:
        failed = [r.name for r in run_checks(con, ALL_CHECKS) if not r.passed]
    assert failed == []


def test_voorbeelddataset_bevat_de_testdagen():
    # De eigen dbt-tests hebben deze dagen nodig (wintertijd, Kerst, Koningsdag, vakanties).
    months = sorted(p.name for p in (SAMPLE / "services").glob("*.csv.gz"))
    assert months == [
        "services-2025-12.csv.gz",
        "services-2026-03.csv.gz",
        "services-2026-04.csv.gz",
        "services-2026-10.csv.gz",
    ]
