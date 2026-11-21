"""Slanke export voor de live app: een paar MB in plaats van de 3 GB database.

Gebruik (na `python -m ov_vertraging.model.train`):
    python -m ov_vertraging.serving.export

Schrijft naar models/serving/:
    timetable.parquet        dienstregeling van de laatste 7 volledige dagen (alle stops)
    hist_route_hour.parquet  historisch % vertraagd per traject x uur (t/m de laatste dag)
    hist_station.parquet     idem per station
    hist_train.parquet       idem per treinnummer
    planned.parquet          gemiddeld aantal geplande vertrekken per station x weekdag x uur
    network.parquet          idem landelijk per weekdag x uur
    stations.parquet         stationscode, naam, UIC-code, coördinaten
    school_holidays.parquet  schoolvakanties per regio (uit de dbt-seed)
    model.txt + model_metadata.json + manifest.json

Idempotent: de map wordt elke keer volledig overschreven.
"""

import argparse
import hashlib
import json
import logging
import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from ov_vertraging.config import DB_PATH, PROJECT_ROOT
from ov_vertraging.model import features as ft

log = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "models"
SERVING_DIR = MODELS_DIR / "serving"
TIMETABLE_DAYS = 7
FULL_DAY_MIN_DEPARTURES = 20_000  # minder = een halve dag aan het eind van de data


def _copy(con: duckdb.DuckDBPyConnection, sql: str, path: Path) -> int:
    con.execute(f"COPY ({sql}) TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    return con.execute(f"SELECT count(*) FROM read_parquet('{path.as_posix()}')").fetchone()[0]


def _smoothed(keys: str, prior_weight: int) -> str:
    """Historisch % over álle beschikbare dagen (= 't/m gisteren' voor een nieuwe dag)."""
    return f"""
        WITH g AS (SELECT avg(is_delayed_5min::int) AS rate FROM {ft.TABLE})
        SELECT {keys}, count(*) AS hist_n,
               (count(*) FILTER (is_delayed_5min) + {prior_weight} * any_value(g.rate))
                   / (count(*) + {prior_weight}) AS hist_pct
        FROM {ft.TABLE}, g GROUP BY ALL
    """


def timetable_days(con: duckdb.DuckDBPyConnection, days: int = TIMETABLE_DAYS) -> list:
    """De laatste `days` volledige dagen in de data (elke weekdag precies één keer)."""
    rows = con.execute(
        """
        SELECT departure_date FROM marts.fct_departures
        GROUP BY 1 HAVING count(*) >= ? ORDER BY 1 DESC LIMIT ?
        """,
        [FULL_DAY_MIN_DEPARTURES, days],
    ).fetchall()
    return sorted(r[0] for r in rows)


def export(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path = SERVING_DIR,
    model_dir: Path = MODELS_DIR,
    prior_weight: int = ft.PRIOR_WEIGHT,
) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    days = timetable_days(con)
    day_list = ", ".join(f"DATE '{d}'" for d in days)
    domestic = ", ".join(f"'{t}'" for t in ft.DOMESTIC_TYPES)
    counts = {}

    counts["timetable"] = _copy(
        con,
        f"""
        WITH stops AS (
            SELECT service_id, train_number, service_type, company, station_code,
                   arrival_time_local, departure_time_local,
                   row_number() OVER w AS stop_sequence,
                   lead(station_code) OVER w AS next_station_code
            FROM staging.stg_services
            WHERE service_date IN ({day_list}) AND is_rail_service
              AND service_type IN ({domestic})
            WINDOW w AS (PARTITION BY service_id
                         ORDER BY coalesce(arrival_time, departure_time), stop_id)
        )
        SELECT service_id, train_number, service_type, company, station_code,
               next_station_code, station_code || '-' || next_station_code AS route_code,
               stop_sequence,
               isodow(coalesce(departure_time_local, arrival_time_local)) AS weekday,
               hour(departure_time_local) * 60 + minute(departure_time_local) AS dep_minute,
               hour(arrival_time_local) * 60 + minute(arrival_time_local) AS arr_minute
        FROM stops
        """,
        out_dir / "timetable.parquet",
    )
    counts["hist_route_hour"] = _copy(
        con,
        _smoothed("route_code, departure_hour", prior_weight),
        out_dir / "hist_route_hour.parquet",
    )
    counts["hist_station"] = _copy(
        con, _smoothed("station_code", prior_weight), out_dir / "hist_station.parquet"
    )
    counts["hist_train"] = _copy(
        con,
        f"""
        WITH t AS (
            SELECT f.train_number, x.is_delayed_5min
            FROM {ft.TABLE} AS x JOIN marts.fct_departures AS f USING (departure_id)
        ),
        g AS (SELECT avg(is_delayed_5min::int) AS rate FROM t)
        SELECT train_number, count(*) AS hist_n,
               (count(*) FILTER (is_delayed_5min) + {prior_weight} * any_value(g.rate))
                   / (count(*) + {prior_weight}) AS hist_pct
        FROM t, g GROUP BY 1
        """,
        out_dir / "hist_train.parquet",
    )
    counts["planned"] = _copy(
        con,
        """
        SELECT station_code, weekday, hour, avg(n) AS planned_station_hour
        FROM (SELECT station_code, departure_date, departure_weekday AS weekday,
                     departure_hour AS hour, count(*) AS n
              FROM marts.fct_departures GROUP BY ALL)
        GROUP BY ALL
        """,
        out_dir / "planned.parquet",
    )
    counts["network"] = _copy(
        con,
        """
        SELECT weekday, hour, avg(n) AS planned_network_hour
        FROM (SELECT departure_date, departure_weekday AS weekday, departure_hour AS hour,
                     count(*) AS n
              FROM marts.fct_departures GROUP BY ALL)
        GROUP BY ALL
        """,
        out_dir / "network.parquet",
    )
    counts["stations"] = _copy(
        con,
        """
        SELECT s.station_code, s.station_name, st.uic_code, s.lat, s.lng, s.is_dutch
        FROM marts.dim_station AS s
        LEFT JOIN staging.stg_stations AS st USING (station_code)
        """,
        out_dir / "stations.parquet",
    )
    counts["school_holidays"] = _copy(
        con,
        "SELECT region, start_date, end_date FROM seeds.nl_school_holidays",
        out_dir / "school_holidays.parquet",
    )

    meta = json.loads((model_dir / "model_metadata.json").read_text())
    shutil.copy(model_dir / meta["model_file"], out_dir / "model.txt")
    (out_dir / "model_metadata.json").write_text(json.dumps(meta, indent=2))
    manifest = {
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_version": meta["version"],
        "timetable_days": [str(d) for d in days],
        "history_until": str(
            con.execute(f"SELECT max(departure_date) FROM {ft.TABLE}").fetchone()[0]
        ),
        "rows": counts,
        "size_mb": round(sum(p.stat().st_size for p in out_dir.iterdir()) / 1e6, 1),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("Serving-export: %s (%.1f MB)", counts, manifest["size_mb"])
    return manifest


def package(serving_dir: Path = SERVING_DIR, out_dir: Path = MODELS_DIR) -> Path:
    """Pak de export in als serving-<modelversie>.tar.gz (voor een GitHub Release).

    Alleen de bekende bestanden gaan erin, zodat er nooit per ongeluk iets anders
    (zoals een .env) in een openbaar bestand belandt. Naast het archief komt een
    .sha256-bestand om de download te kunnen controleren.
    """
    manifest = json.loads((serving_dir / "manifest.json").read_text())
    names = [
        f"{t}.parquet"
        for t in (
            "timetable",
            "hist_route_hour",
            "hist_station",
            "hist_train",
            "planned",
            "network",
            "stations",
            "school_holidays",
        )
    ] + ["model.txt", "model_metadata.json", "manifest.json"]
    archive = out_dir / f"serving-{manifest['model_version']}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name in names:
            tar.add(serving_dir / name, arcname=name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".gz.sha256").write_text(f"{digest}  {archive.name}\n")
    log.info(
        "Ingepakt: %s (%.1f MB), sha256 %s", archive.name, archive.stat().st_size / 1e6, digest[:12]
    )
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description="Maak de serving-export voor de API.")
    parser.add_argument(
        "--package",
        action="store_true",
        help="Pak de bestaande export ook in als tar.gz (voor een Release).",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        export(con)
    if args.package:
        package()


if __name__ == "__main__":
    main()
