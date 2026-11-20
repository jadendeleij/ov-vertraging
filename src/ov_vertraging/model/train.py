"""Train baseline, logistische regressie en LightGBM; evalueer op de testset.

Eén commando, reproduceerbaar (vaste seeds):
    python -m ov_vertraging.model.train

Stappen:
1. ml.departure_features (opnieuw) bouwen in DuckDB.
2. Train-steekproef (nov–jul), volledige validatie (aug) en test (sep–okt) laden.
3. Baseline, logistische regressie en LightGBM (kleine hyperparameter-zoektocht op
   de validatieset); alle runs gelogd in MLflow (sqlite:///mlflow.db).
4. LightGBM opnieuw trainen op train + validatie; alles evalueren op de testset.
5. Model + metadata naar models/, grafieken naar docs/img/, tabel naar
   docs/model_results.md.

Bekijk de runs met:  mlflow ui --backend-store-uri sqlite:///mlflow.db
"""

import argparse
import gc
import json
import logging
import time
from datetime import UTC, datetime

import duckdb
import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ov_vertraging.config import DB_PATH, PROJECT_ROOT
from ov_vertraging.model import evaluate as ev
from ov_vertraging.model import features as ft

log = logging.getLogger(__name__)

SEED = 42
TRAIN_SAMPLE = 3_000_000  # past binnen 8 GB werkgeheugen
LOGREG_SAMPLE = 1_000_000
MLFLOW_URI = f"sqlite:///{PROJECT_ROOT / 'mlflow.db'}"
EXPERIMENT = "ov-vertraging"

# Kleine, bewuste zoektocht: van eenvoudig naar complexer. Early stopping op de
# validatieset bepaalt het aantal bomen.
LGBM_BASE = {
    "objective": "binary",
    "metric": "average_precision",
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "cat_smooth": 50,
    "verbose": -1,
    "seed": SEED,
    "num_threads": 8,
}
LGBM_GRID = [
    {"num_leaves": 31, "min_child_samples": 200},
    {"num_leaves": 127, "min_child_samples": 200},
    {"num_leaves": 127, "min_child_samples": 1000},
    {"num_leaves": 255, "min_child_samples": 1000},
]

# Logistische regressie: alleen features die lineair zinvol zijn. De ~3.000 trajecten
# en ~400 stations zitten er niet als dummy in; hun informatie zit in de historische
# features.
LOGREG_ONEHOT = ["service_type", "company", "departure_hour", "departure_weekday", "month"]
LOGREG_NUMERIC = [c for c in ft.NUMERIC if c not in LOGREG_ONEHOT]


# ---------- baseline ----------


def baseline_table(con: duckdb.DuckDBPyConnection, prior_weight: int = 50) -> pd.DataFrame:
    """% vertraagd per traject x uur over de hele trainingsperiode (gesmoothed)."""
    period = ft.SPLITS["train"]
    return con.execute(
        f"""
        WITH t AS (
            SELECT route_code, departure_hour, is_delayed_5min
            FROM {ft.TABLE} WHERE departure_date BETWEEN ? AND ?
        ),
        g AS (SELECT avg(is_delayed_5min::int) AS rate FROM t)
        SELECT route_code, departure_hour,
               (count(*) FILTER (is_delayed_5min) + ? * any_value(g.rate))
                   / (count(*) + ?) AS p_baseline,
               any_value(g.rate) AS global_rate
        FROM t, g GROUP BY 1, 2
        """,
        [period.start, period.end, prior_weight, prior_weight],
    ).df()


def predict_baseline(table: pd.DataFrame, df: pd.DataFrame) -> np.ndarray:
    keys = df[["route_code", "departure_hour"]].astype({"route_code": str})
    t = table.astype({"route_code": str, "departure_hour": "float32"})
    merged = keys.merge(t, on=["route_code", "departure_hour"], how="left")
    return merged.p_baseline.fillna(table.global_rate.iloc[0]).to_numpy()


# ---------- logistische regressie ----------


def logreg_pipeline() -> Pipeline:
    pre = ColumnTransformer(
        [
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=100), LOGREG_ONEHOT),
            (
                "num",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median", add_indicator=True)),
                        ("scale", StandardScaler()),
                    ]
                ),
                LOGREG_NUMERIC,
            ),
        ]
    )
    return Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=500, C=1.0))])


def _logreg_frame(df: pd.DataFrame) -> pd.DataFrame:
    X = df[LOGREG_ONEHOT + LOGREG_NUMERIC].copy()
    for col in ["departure_hour", "departure_weekday", "month"]:
        X[col] = X[col].astype(int).astype(str)  # als categorie, niet als getal
    for col in ["service_type", "company"]:
        X[col] = X[col].astype(str)
    return X


# ---------- LightGBM ----------


def lgb_dataset(df: pd.DataFrame, reference: lgb.Dataset | None = None) -> lgb.Dataset:
    return lgb.Dataset(
        df[ft.FEATURES],
        label=df[ft.TARGET],
        categorical_feature=ft.CATEGORICAL,
        reference=reference,
        free_raw_data=False,
    )


def search_lightgbm(train: pd.DataFrame, val: pd.DataFrame) -> tuple[dict, int, dict]:
    """Probeer LGBM_GRID; kies op PR-AUC op de validatieset."""
    d_train = lgb_dataset(train)
    d_val = lgb_dataset(val, reference=d_train)
    best = (None, 0, {"pr_auc": -1})
    for i, extra in enumerate(LGBM_GRID, start=1):
        params = {**LGBM_BASE, **extra}
        t0 = time.perf_counter()
        with mlflow.start_run(run_name=f"lightgbm-zoek-{i}", nested=True):
            booster = lgb.train(
                params,
                d_train,
                num_boost_round=2000,
                valid_sets=[d_val],
                callbacks=[lgb.early_stopping(100, verbose=False)],
            )
            p_val = booster.predict(val[ft.FEATURES], num_iteration=booster.best_iteration)
            s = ev.scores(val[ft.TARGET].to_numpy(), p_val)
            mlflow.log_params({**extra, "best_iteration": booster.best_iteration})
            mlflow.log_metrics({f"val_{k}": v for k, v in s.items() if k != "n"})
        log.info(
            "LightGBM %d/%d %s: PR-AUC %.4f, %d bomen, %.0f s",
            i,
            len(LGBM_GRID),
            extra,
            s["pr_auc"],
            booster.best_iteration,
            time.perf_counter() - t0,
        )
        if s["pr_auc"] > best[2]["pr_auc"]:
            best = (params, booster.best_iteration, s)
    return best


# ---------- hoofdprogramma ----------


def main(skip_features: bool = False) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ev.MODELS_DIR.mkdir(exist_ok=True)
    ev.IMG_DIR.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(MLFLOW_URI)
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(
            EXPERIMENT, artifact_location=(PROJECT_ROOT / "mlartifacts").as_uri()
        )
    mlflow.set_experiment(EXPERIMENT)

    with duckdb.connect(str(DB_PATH)) as con:
        if not skip_features:
            ft.build_feature_table(con)
        train = ft.load_split(con, "train", sample_n=TRAIN_SAMPLE, seed=SEED)
        val = ft.load_split(con, "validation")
        base_table = baseline_table(con)
    categories = ft.category_lists(pd.concat([train[ft.CATEGORICAL], val[ft.CATEGORICAL]]))
    train = ft.align_categories(train, categories)
    val = ft.align_categories(val, categories)
    log.info("Train-steekproef: %d, validatie: %d", len(train), len(val))

    with mlflow.start_run(run_name=f"training-{datetime.now(UTC):%Y%m%d-%H%M}"):
        mlflow.log_params(
            {"train_sample": TRAIN_SAMPLE, "n_features": len(ft.FEATURES), "seed": SEED}
        )
        y_val = val[ft.TARGET].to_numpy()

        # 1. Baseline
        with mlflow.start_run(run_name="baseline", nested=True):
            s = ev.scores(y_val, predict_baseline(base_table, val))
            mlflow.log_metrics({f"val_{k}": v for k, v in s.items() if k != "n"})
            log.info("Baseline: PR-AUC %.4f", s["pr_auc"])

        # 2. Logistische regressie (op een kleinere steekproef: lineair model, snel genoeg)
        with mlflow.start_run(run_name="logistische-regressie", nested=True):
            lr_train = train.sample(n=min(LOGREG_SAMPLE, len(train)), random_state=SEED)
            logreg = logreg_pipeline().fit(_logreg_frame(lr_train), lr_train[ft.TARGET])
            s = ev.scores(y_val, logreg.predict_proba(_logreg_frame(val))[:, 1])
            mlflow.log_params({"sample": len(lr_train), "C": 1.0})
            mlflow.log_metrics({f"val_{k}": v for k, v in s.items() if k != "n"})
            log.info("Logistische regressie: PR-AUC %.4f", s["pr_auc"])
            del lr_train

        # 3. LightGBM: zoeken op validatie, daarna opnieuw op train + validatie
        params, n_trees, best_val = search_lightgbm(train, val)
        log.info("Beste LightGBM: %s met %d bomen", params, n_trees)
        full = pd.concat([train, val], ignore_index=True)
        del train
        gc.collect()
        with mlflow.start_run(run_name="lightgbm-finaal", nested=True):
            booster = lgb.train(params, lgb_dataset(full), num_boost_round=n_trees)
            mlflow.log_params({k: v for k, v in params.items() if k != "verbose"})
            mlflow.log_param("num_boost_round", n_trees)
        del full
        gc.collect()

        # 4. Evaluatie op de testset (volledig)
        with duckdb.connect(str(DB_PATH), read_only=True) as con:
            test = ft.align_categories(ft.load_split(con, "test"), categories)
        y_test = test[ft.TARGET].to_numpy()
        preds = {
            "baseline": predict_baseline(base_table, test),
            "logistische regressie": logreg.predict_proba(_logreg_frame(test))[:, 1],
            "lightgbm": booster.predict(test[ft.FEATURES]),
        }
        test_scores = {name: ev.scores(y_test, p) for name, p in preds.items()}
        for name, s in test_scores.items():
            log.info(
                "TEST %-22s PR-AUC %.4f  ROC-AUC %.4f  Brier %.5f",
                name,
                s["pr_auc"],
                s["roc_auc"],
                s["brier"],
            )
            mlflow.log_metrics(
                {f"test_{name.replace(' ', '_')}_{k}": v for k, v in s.items() if k != "n"}
            )

        p_lgb = preds["lightgbm"]
        test["month"] = test["month"].astype(int)
        by_type = ev.by_group(test, p_lgb, "service_type")
        by_month = ev.by_group(test, p_lgb, "month")
        worst_days = ev.error_analysis(test, p_lgb)
        ev.calibration_plot(preds, y_test, ev.IMG_DIR / "calibration.png")
        shap_sample = test.sample(n=20_000, random_state=SEED)[ft.FEATURES]
        importance = ev.shap_summary(booster, shap_sample, ev.IMG_DIR / "shap_summary.png")
        for img in ("calibration.png", "shap_summary.png"):
            mlflow.log_artifact(str(ev.IMG_DIR / img))

        # 5. Opslaan
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        model_file = f"lightgbm_{stamp}.txt"
        booster.save_model(str(ev.MODELS_DIR / model_file))
        metadata = {
            "model_file": model_file,
            "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "version": stamp,
            "target": ft.TARGET,
            "features": ft.FEATURES,
            "categorical": ft.CATEGORICAL,
            "categories": categories,
            "params": {k: v for k, v in params.items() if k != "verbose"},
            "num_boost_round": n_trees,
            "train_period": [str(ft.SPLITS["train"].start), str(ft.SPLITS["validation"].end)],
            "test_period": [str(ft.SPLITS["test"].start), str(ft.SPLITS["test"].end)],
            "train_sample": TRAIN_SAMPLE,
            "validation_scores": best_val,
            "test_scores": test_scores,
        }
        (ev.MODELS_DIR / "model_metadata.json").write_text(json.dumps(metadata, indent=2))
        mlflow.log_artifact(str(ev.MODELS_DIR / "model_metadata.json"))

        md = ev.results_markdown(test_scores, by_type, by_month, worst_days, importance)
        (PROJECT_ROOT / "docs" / "model_results.md").write_text(md)
        log.info("Klaar: models/%s, docs/model_results.md, docs/img/*.png", model_file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train en evalueer het vertragingsmodel.")
    parser.add_argument(
        "--skip-features",
        action="store_true",
        help="Gebruik de bestaande ml.departure_features (scheelt ~2 minuten).",
    )
    main(skip_features=parser.parse_args().skip_features)
