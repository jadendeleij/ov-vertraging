"""Tests voor de modelmonitoring: incrementeel scoren en dagelijkse metrieken."""

import json
from datetime import date, timedelta

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from ov_vertraging import dashboard_queries as dq
from ov_vertraging import monitoring
from ov_vertraging.model import features as ft

CATS = {
    "service_type": ["Intercity", "Sprinter"],
    "company": ["NS"],
    "station_code": ["A", "B"],
    "next_station_code": ["B", "C"],
    "route_code": ["A-B", "B-C"],
}
TRAIN_END = date(2026, 8, 31)
DAYS = [date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2)]  # 1 trainingsdag + 2 nieuwe


def _frame(n: int, rng) -> pd.DataFrame:
    X = pd.DataFrame({c: rng.random(n).astype("float32") for c in ft.NUMERIC})
    for col, cats in CATS.items():
        X[col] = pd.Categorical(rng.choice(cats, n), categories=cats)
    return X


@pytest.fixture
def setup(tmp_path):
    rng = np.random.default_rng(0)
    # Mini-model: vertraagd als hist_route_hour_pct hoog is.
    X = _frame(500, rng)
    booster = lgb.train(
        {"objective": "binary", "verbose": -1, "min_data_in_leaf": 5},
        lgb.Dataset(
            X[ft.FEATURES],
            label=(X.hist_route_hour_pct > 0.8).astype(int),
            categorical_feature=ft.CATEGORICAL,
        ),
        num_boost_round=10,
    )
    booster.save_model(str(tmp_path / "m.txt"))
    (tmp_path / "model_metadata.json").write_text(
        json.dumps(
            {
                "model_file": "m.txt",
                "version": "v1",
                "categories": CATS,
                "train_period": ["2025-11-01", str(TRAIN_END)],
            }
        )
    )

    # Featuretabel: 200 vertrekken per dag.
    con = duckdb.connect()
    con.execute("CREATE SCHEMA ml")
    rows = []
    for i, day in enumerate(DAYS):
        df = _frame(200, rng)
        df.insert(0, "departure_id", range(i * 1000, i * 1000 + 200))
        df.insert(1, "departure_date", day)
        df.insert(2, ft.TARGET, df.hist_route_hour_pct > 0.8)
        rows.append(df)
    table = pd.concat(rows)
    for col in ft.CATEGORICAL:
        table[col] = table[col].astype(str)
    con.register("t", table)
    con.execute(f"CREATE TABLE {ft.TABLE} AS SELECT * FROM t")
    return con, tmp_path


def test_scoort_alleen_dagen_na_de_training(setup):
    con, model_dir = setup
    result = monitoring.score_new_days(con, model_dir)
    assert result == {"days": 2, "rows": 400, "model_version": "v1"}
    first = con.execute(f"SELECT min(departure_date) FROM {monitoring.PREDICTIONS}").fetchone()[0]
    assert first == TRAIN_END + timedelta(days=1)


def test_tweede_run_scoort_niets_dubbel(setup):
    con, model_dir = setup
    monitoring.score_new_days(con, model_dir)
    assert monitoring.score_new_days(con, model_dir)["rows"] == 0
    n = con.execute(f"SELECT count(*) FROM {monitoring.PREDICTIONS}").fetchone()[0]
    assert n == 400


def test_zonder_model_geen_fout(setup, tmp_path):
    con, _ = setup
    empty = tmp_path / "leeg"
    empty.mkdir()
    assert monitoring.score_new_days(con, empty)["model_version"] is None


def test_dagelijkse_metrieken(setup, monkeypatch):
    con, model_dir = setup
    monkeypatch.setattr(monitoring, "FULL_DAY_MIN", 100)
    monitoring.score_new_days(con, model_dir)
    daily = monitoring.daily_metrics(con)
    assert len(daily) == 2
    assert (daily.vertrekken == 200).all()
    assert daily.pr_auc.between(0.5, 1).all()  # het mini-model kent het patroon
    assert daily.volledige_dag.all()
    typ = con.execute(f"SELECT typeof(datum) FROM {monitoring.DAILY} LIMIT 1").fetchone()[0]
    assert typ == "DATE"


def test_kalibratie_ratio_weegt_naar_vertrekken_en_negeert_halve_dagen(monkeypatch):
    monkeypatch.setattr(monitoring, "FULL_DAY_MIN", 100)
    daily = pd.DataFrame(
        {
            "datum": [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)],
            "vertrekken": [100, 300, 10],  # laatste = halve dag
            "werkelijk": [0.04, 0.04, 0.50],
            "voorspeld": [0.02, 0.04, 0.01],
        }
    )
    # (0,02*100 + 0,04*300) / (0,04*400) = 14 / 16
    assert monitoring.calibration_ratio(daily) == pytest.approx(14 / 16)


def test_dashboard_query_leeg_zonder_tabel():
    assert dq.monitoring_daily(duckdb.connect()).empty
