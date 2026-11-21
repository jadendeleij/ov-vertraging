"""Monitoring: hoe goed voorspelt het model dagen die het nog nooit zag?

Draait dagelijks via Dagster (assets model_predictions en model_monitoring), of los:
    python -m ov_vertraging.monitoring

1. Voorspel met het opgeslagen model (models/) álle vertrekken van dagen ná de
   trainingsperiode die nog niet gescoord zijn -> monitoring.predictions.
   Incrementeel en idempotent: per (dag, modelversie) precies één keer.
2. Vat per dag samen: gemiddeld voorspeld vs. werkelijk % vertraagd, PR-AUC,
   ROC-AUC en Brier -> monitoring.daily.

Let op: de werkelijke uitkomsten komen per maand binnen (Rijden de Treinen publiceert
een maand een paar dagen na afloop). De monitoring loopt dus tot ~1 maand achter. En
het weer is hier gemeten, niet voorspeld: de live app scoort in de praktijk iets lager.
"""

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd

from ov_vertraging.config import DB_PATH, PROJECT_ROOT
from ov_vertraging.model import features as ft

log = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "models"
PREDICTIONS = "monitoring.predictions"
DAILY = "monitoring.daily"
# Kalibratie-alarm: gemiddeld voorspeld / werkelijk buiten deze marge (laatste 30 dagen).
CALIBRATION_RANGE = (0.67, 1.5)
# Minder vertrekken = een halve dag (bv. alleen nachttreinen na middernacht).
FULL_DAY_MIN = 10_000


def load_model(model_dir: Path = MODELS_DIR) -> tuple[lgb.Booster, dict] | None:
    meta_path = model_dir / "model_metadata.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    return lgb.Booster(model_file=str(model_dir / meta["model_file"])), meta


def _ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS monitoring")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PREDICTIONS} (
            departure_id BIGINT,
            departure_date DATE,
            service_type VARCHAR,
            is_delayed_5min BOOLEAN,
            probability DOUBLE,
            model_version VARCHAR,
            scored_at TIMESTAMP
        )
        """
    )


def unscored_days(con: duckdb.DuckDBPyConnection, version: str, after: date) -> list[date]:
    """Dagen ná de trainingsperiode die voor deze modelversie nog niet gescoord zijn."""
    _ensure_tables(con)
    rows = con.execute(
        f"""
        SELECT DISTINCT departure_date FROM {ft.TABLE}
        WHERE departure_date > ?
          AND departure_date NOT IN (
              SELECT departure_date FROM {PREDICTIONS} WHERE model_version = ?
          )
        ORDER BY 1
        """,
        [after, version],
    ).fetchall()
    return [r[0] for r in rows]


def score_new_days(con: duckdb.DuckDBPyConnection, model_dir: Path = MODELS_DIR) -> dict:
    """Voorspel per dag alle nog niet gescoorde vertrekken. Per dag, zodat het geheugen
    klein blijft (~50.000 vertrekken per keer)."""
    loaded = load_model(model_dir)
    if loaded is None:
        log.warning(
            "Geen model in %s; train eerst (python -m ov_vertraging.model.train)", model_dir
        )
        return {"days": 0, "rows": 0, "model_version": None}
    booster, meta = loaded
    version = meta["version"]
    train_end = date.fromisoformat(meta["train_period"][1])
    days = unscored_days(con, version, train_end)
    total = 0
    for day in days:
        df = ft.align_categories(ft.load_period(con, day, day), meta["categories"])
        out = pd.DataFrame(
            {
                "departure_id": df["departure_id"],
                "departure_date": df["departure_date"],
                "service_type": df["service_type"].astype(str),
                "is_delayed_5min": df[ft.TARGET].astype(bool),
                "probability": booster.predict(df[ft.FEATURES]),
                "model_version": version,
                "scored_at": datetime.now(UTC).replace(tzinfo=None),
            }
        )
        con.register("new_predictions", out)
        con.execute(f"INSERT INTO {PREDICTIONS} SELECT * FROM new_predictions")
        con.unregister("new_predictions")
        total += len(out)
    log.info("Model %s: %d nieuwe dagen gescoord (%d vertrekken)", version, len(days), total)
    return {"days": len(days), "rows": total, "model_version": version}


def daily_metrics(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Per dag en modelversie: voorspeld vs. werkelijk, PR-AUC, ROC-AUC en Brier."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    _ensure_tables(con)
    base = con.execute(
        f"""
        SELECT departure_date AS datum, model_version, count(*) AS vertrekken,
               avg(is_delayed_5min::int) AS werkelijk,
               avg(probability) AS voorspeld,
               avg(power(probability - is_delayed_5min::int, 2)) AS brier
        FROM {PREDICTIONS} GROUP BY ALL ORDER BY ALL
        """
    ).df()
    pr, roc = [], []
    for row in base.itertuples():
        y, p = (
            np.asarray(a)
            for a in zip(
                *con.execute(
                    f"SELECT is_delayed_5min::int, probability FROM {PREDICTIONS} "
                    "WHERE departure_date = ? AND model_version = ?",
                    [row.datum, row.model_version],
                ).fetchall(),
                strict=True,
            )
        )
        both = 0 < y.sum() < len(y)  # beide klassen nodig voor AUC
        pr.append(float(average_precision_score(y, p)) if both else None)
        roc.append(float(roc_auc_score(y, p)) if both else None)
    base["pr_auc"] = pr
    base["roc_auc"] = roc
    base["volledige_dag"] = base.vertrekken >= FULL_DAY_MIN
    con.register("daily_df", base)
    con.execute(
        f"CREATE OR REPLACE TABLE {DAILY} AS "
        "SELECT * REPLACE (CAST(datum AS DATE) AS datum) FROM daily_df"
    )
    con.unregister("daily_df")
    return base


def calibration_ratio(daily: pd.DataFrame, days: int = 30) -> float | None:
    """Gemiddeld voorspeld / werkelijk over de laatste `days` dagen (gewogen)."""
    full = daily[daily.vertrekken >= FULL_DAY_MIN]
    if full.empty:
        return None
    recent = full.sort_values("datum").tail(days)
    actual = (recent.werkelijk * recent.vertrekken).sum()
    predicted = (recent.voorspeld * recent.vertrekken).sum()
    return float(predicted / actual) if actual else None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with duckdb.connect(str(DB_PATH)) as con:
        score_new_days(con)
        daily = daily_metrics(con)
    ratio = calibration_ratio(daily)
    log.info(
        "%d dagen in monitoring; kalibratie laatste 30 dagen: %s",
        len(daily),
        "-" if ratio is None else f"{ratio:.2f}",
    )


if __name__ == "__main__":
    main()
