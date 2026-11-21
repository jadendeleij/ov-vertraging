"""Tests voor de Dagster-definities en het dagelijkse maandvenster. Geen internet."""

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from ov_vertraging.download import recent_months

TRANSFORM = Path(__file__).parents[1] / "transform"


def test_recent_months_normaal():
    assert recent_months(date(2026, 11, 20)) == ["2026-10", "2026-11"]


def test_recent_months_over_jaargrens():
    assert recent_months(date(2027, 1, 3)) == ["2026-12", "2027-01"]
    assert recent_months(date(2027, 1, 3), n=3) == ["2026-11", "2026-12", "2027-01"]


@pytest.fixture(scope="module")
def defs():
    # dagster-dbt heeft het dbt-manifest nodig; `dbt parse` maakt het zonder database.
    if not (TRANSFORM / "target" / "manifest.json").exists():
        dbt = Path(sys.executable).parent / "dbt"
        subprocess.run([str(dbt), "parse", "-q"], cwd=TRANSFORM, check=True)
    from orchestration.definitions import defs

    return defs


def test_alle_lagen_staan_in_de_lineage(defs):
    keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    for expected in [
        "services_files",
        "weather_files",
        "stations_file",
        "raw/services",
        "raw/weather_hourly",
        "staging/stg_services",
        "intermediate/int_departures_with_weather",
        "marts/fct_departures",
        "marts/dim_date",
        "ml_features",
        "model_predictions",
        "model_monitoring",
    ]:
        assert expected in keys


def test_raw_tabel_hangt_aan_download_en_dbt_aan_raw(defs):
    graph = defs.resolve_asset_graph()
    by_name = {k.to_user_string(): k for k in graph.get_all_asset_keys()}

    parents_raw = {p.to_user_string() for p in graph.get(by_name["raw/services"]).parent_keys}
    assert parents_raw == {"services_files"}

    parents_stg = {
        p.to_user_string() for p in graph.get(by_name["staging/stg_services"]).parent_keys
    }
    assert "raw/services" in parents_stg


def test_kwaliteitschecks_zijn_asset_checks(defs):
    checks = {c.name for c in defs.resolve_asset_graph().asset_check_keys}
    assert {"row_count", "unique_stop_id", "weather_unique_hour"} <= checks
    assert "kalibratie_laatste_30_dagen" in checks


def test_dagelijks_schema_staat_aan(defs):
    schedule = defs.get_schedule_def("dagelijks_07u")
    assert schedule.cron_schedule == "0 7 * * *"
    assert schedule.execution_timezone == "Europe/Amsterdam"
    assert schedule.default_status.value == "RUNNING"
