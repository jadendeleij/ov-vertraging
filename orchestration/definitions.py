"""Dagster-definities: de hele pipeline als assets, met een dagelijks schema.

Starten (vanuit de projectroot):
    dagster dev
en open http://localhost:3000. workspace.yaml in de root wijst naar dit bestand.

Lineage in de UI:
    downloads (treinen, stations, weer)
        -> raw-tabellen in DuckDB (+ kwaliteitschecks als asset checks)
        -> dbt: seeds -> staging -> intermediate -> marts

Elke stap is idempotent, dus het schema mag dagelijks alles opnieuw draaien:
downloads slaan bestaande bestanden over, raw-tabellen en dbt-modellen worden met
CREATE OR REPLACE opnieuw opgebouwd.
"""

import os
import sys
from datetime import date
from pathlib import Path

import duckdb
import requests
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetCheckSpec,
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    AssetSpec,
    Config,
    DefaultScheduleStatus,
    Definitions,
    MaterializeResult,
    MetadataValue,
    ScheduleDefinition,
    asset,
    define_asset_job,
    multi_asset,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from ov_vertraging import download, download_weather, load, load_weather, quality
from ov_vertraging.config import DB_PATH, PROJECT_ROOT

TIMEZONE = "Europe/Amsterdam"
DBT_PROJECT_DIR = PROJECT_ROOT / "transform"

# dbt leest het databasepad uit deze variabele; absoluut maken zodat het niet
# afhangt van de map waarin Dagster dbt start.
os.environ.setdefault("OV_DUCKDB_PATH", str(DB_PATH))
# dbt staat in dezelfde venv als Python. Zet die map voorin PATH, zodat dagster-dbt
# hem ook vindt als de venv niet geactiveerd is (bv. bij `dagster dev` of een schema).
VENV_BIN = str(Path(sys.executable).parent)
if VENV_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = VENV_BIN + os.pathsep + os.environ.get("PATH", "")


# ---------- downloads ----------


class MonthsConfig(Config):
    """Welke maanden ophalen. Leeg = vorige en huidige maand (dagelijks schema).
    Voor een eerste vulling bv. start='2025-11', end='2026-10'."""

    start: str = ""
    end: str = ""


def _months(config: MonthsConfig) -> list[str]:
    if config.start and config.end:
        return download.month_range(config.start, config.end)
    return download.recent_months(date.today())


def _file_metadata(paths: list[Path]) -> dict:
    return {
        "bestanden": MetadataValue.int(len(paths)),
        "grootte_mb": MetadataValue.float(round(sum(p.stat().st_size for p in paths) / 1e6, 1)),
        "laatste": MetadataValue.text(paths[-1].name if paths else "-"),
    }


@asset(group_name="download", kinds={"python"})
def services_files(context: AssetExecutionContext, config: MonthsConfig) -> MaterializeResult:
    """Maandbestanden van het Rijden de Treinen treinarchief in data/raw/services/."""
    for month in _months(config):
        try:
            download.download_month(month)
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code == 404:
                # Een maand verschijnt pas een paar dagen na afloop.
                context.log.info(f"{month} is nog niet gepubliceerd, overgeslagen")
            else:
                raise
    return MaterializeResult(metadata=_file_metadata(sorted(download.SERVICES_DIR.glob("*.gz"))))


@asset(group_name="download", kinds={"python"})
def stations_file() -> MaterializeResult:
    """Stationsbestand van Rijden de Treinen."""
    path = download.download_stations()
    return MaterializeResult(metadata=_file_metadata([path]))


@asset(group_name="download", kinds={"python"})
def weather_files(config: MonthsConfig) -> MaterializeResult:
    """KNMI-uurgegevens per maand in data/raw/weather/. Onvolledige maanden worden
    ververst tot ze definitief zijn."""
    for month in _months(config):
        download_weather.download_weather_month(month)
    paths = sorted(download_weather.WEATHER_DIR.glob("knmi-uur-*.txt"))
    return MaterializeResult(metadata=_file_metadata(paths))


# ---------- raw-tabellen + kwaliteitschecks ----------

# Asset keys gelijk aan de dbt-sources (source 'raw'), zodat Dagster de lineage
# van download -> raw -> dbt vanzelf aan elkaar knoopt.
RAW_SERVICES = AssetKey(["raw", "services"])
RAW_STATIONS = AssetKey(["raw", "stations"])
RAW_WEATHER_HOURLY = AssetKey(["raw", "weather_hourly"])
RAW_WEATHER_STATIONS = AssetKey(["raw", "weather_stations"])

CHECKS_PER_ASSET = {
    RAW_SERVICES: quality.SERVICE_CHECKS,
    RAW_STATIONS: quality.STATION_CHECKS,
    RAW_WEATHER_HOURLY: quality.WEATHER_CHECKS,
}


def _check_name(check_fn) -> str:
    return check_fn.__name__.removeprefix("check_")


@multi_asset(
    specs=[
        AssetSpec(
            RAW_SERVICES,
            deps=[services_files],
            group_name="raw",
            kinds={"duckdb"},
            description="Treinarchief: één rij per stop van een rit.",
        ),
        AssetSpec(
            RAW_STATIONS,
            deps=[stations_file],
            group_name="raw",
            kinds={"duckdb"},
            description="Stationsbestand met coördinaten.",
        ),
        AssetSpec(
            RAW_WEATHER_HOURLY,
            deps=[weather_files],
            group_name="raw",
            kinds={"duckdb"},
            description="KNMI-uurwaarden per weerstation (bron-eenheden).",
        ),
        AssetSpec(
            RAW_WEATHER_STATIONS,
            deps=[weather_files],
            group_name="raw",
            kinds={"duckdb"},
            description="KNMI-weerstations met coördinaten.",
        ),
    ],
    check_specs=[
        AssetCheckSpec(_check_name(fn), asset=key, blocking=True)
        for key, fns in CHECKS_PER_ASSET.items()
        for fn in fns
    ],
)
def raw_tables():
    """Laad alle bestanden in DuckDB en draai de datakwaliteitschecks.

    blocking=True: faalt een check, dan draait dbt niet op kapotte data.
    """
    counts = {
        RAW_SERVICES: load.load_services(),
        RAW_STATIONS: load.load_stations(),
        RAW_WEATHER_HOURLY: load_weather.load_weather(),
    }
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        counts[RAW_WEATHER_STATIONS] = con.execute(
            "SELECT count(*) FROM raw.weather_stations"
        ).fetchone()[0]
        for key, n in counts.items():
            yield MaterializeResult(asset_key=key, metadata={"rijen": MetadataValue.int(n)})
        for key, fns in CHECKS_PER_ASSET.items():
            for fn in fns:
                result = fn(con)
                yield AssetCheckResult(
                    asset_key=key,
                    check_name=_check_name(fn),
                    passed=result.passed,
                    severity=AssetCheckSeverity.ERROR,
                    metadata={"detail": MetadataValue.text(result.detail)},
                )


# ---------- dbt ----------

dbt_project = DbtProject(project_dir=DBT_PROJECT_DIR, profiles_dir=DBT_PROJECT_DIR)
# Tijdens `dagster dev` maakt dit het dbt-manifest (dbt parse) automatisch aan.
dbt_project.prepare_if_dev()


class OvDbtTranslator(DagsterDbtTranslator):
    """Groepeer dbt-modellen in de UI per laag (staging/intermediate/marts/seeds)."""

    def get_group_name(self, dbt_resource_props: dict) -> str | None:
        if dbt_resource_props["resource_type"] == "seed":
            return "seeds"
        fqn = dbt_resource_props.get("fqn", [])
        return fqn[1] if len(fqn) > 2 else None


@dbt_assets(manifest=dbt_project.manifest_path, dagster_dbt_translator=OvDbtTranslator())
def ov_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    """Alle dbt-seeds en -modellen; dbt-tests verschijnen als asset checks."""
    yield from dbt.cli(["build"], context=context).stream()


# ---------- job + schema ----------

dagelijkse_pipeline = define_asset_job(
    name="dagelijkse_pipeline",
    selection=AssetSelection.all(),
    description="Download nieuwe data, laad in DuckDB, draai checks en alle dbt-modellen.",
)

dagelijks_schema = ScheduleDefinition(
    name="dagelijks_07u",
    job=dagelijkse_pipeline,
    cron_schedule="0 7 * * *",  # elke dag om 07:00
    execution_timezone=TIMEZONE,
    default_status=DefaultScheduleStatus.RUNNING,  # staat meteen aan
)

defs = Definitions(
    assets=[services_files, stations_file, weather_files, raw_tables, ov_dbt_assets],
    jobs=[dagelijkse_pipeline],
    schedules=[dagelijks_schema],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
