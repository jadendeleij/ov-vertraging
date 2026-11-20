"""Tests voor de metrieken en hulpfuncties van training en evaluatie (kleine data)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ov_vertraging.model import evaluate as ev
from ov_vertraging.model import features as ft
from ov_vertraging.model import train as tr


def test_scores_perfect_en_willekeurig():
    y = np.array([0, 0, 0, 1])
    perfect = ev.scores(y, np.array([0.0, 0.0, 0.0, 1.0]))
    assert perfect["pr_auc"] == pytest.approx(1.0)
    assert perfect["roc_auc"] == pytest.approx(1.0)
    assert perfect["brier"] == pytest.approx(0.0, abs=1e-6)
    constant = ev.scores(y, np.full(4, 0.25))
    assert constant["roc_auc"] == pytest.approx(0.5)
    assert constant["base_rate"] == pytest.approx(0.25)


def test_by_group_slaat_kleine_en_eenzijdige_groepen_over():
    df = pd.DataFrame({"service_type": ["IC"] * 4 + ["SPR"] * 2, ft.TARGET: [0, 1, 0, 1, 0, 0]})
    out = ev.by_group(df, np.array([0.1, 0.9, 0.2, 0.8, 0.1, 0.1]), "service_type", min_n=2)
    assert list(out.service_type) == ["IC"]  # SPR heeft alleen nullen
    assert out.roc_auc.iloc[0] == pytest.approx(1.0)


def test_error_analysis_vindt_onderschatte_dag():
    df = pd.DataFrame(
        {
            "departure_date": [date(2026, 9, 1)] * 10_000 + [date(2026, 9, 2)] * 10_000,
            ft.TARGET: [0] * 10_000 + [1] * 5_000 + [0] * 5_000,
        }
    )
    worst = ev.error_analysis(df, np.full(20_000, 0.05), top=1)
    assert worst.datum.iloc[0] == date(2026, 9, 2)
    assert worst.verschil.iloc[0] == pytest.approx(0.45)


def test_align_categories_zelfde_codes_en_onbekend_wordt_nan():
    train = pd.DataFrame({c: pd.Categorical(["A", "B"]) for c in ft.CATEGORICAL})
    cats = ft.category_lists(train)
    test = pd.DataFrame({c: pd.Categorical(["B", "NIEUW"]) for c in ft.CATEGORICAL})
    out = ft.align_categories(test, cats)
    col = ft.CATEGORICAL[0]
    assert out[col].cat.codes.tolist() == [1, -1]  # 'B' houdt code 1, 'NIEUW' = NaN


def test_baseline_voorspelling_met_terugval_op_gemiddelde():
    table = pd.DataFrame(
        {"route_code": ["A-B"], "departure_hour": [8], "p_baseline": [0.2], "global_rate": [0.04]}
    )
    df = pd.DataFrame(
        {"route_code": pd.Categorical(["A-B", "X-Y"]), "departure_hour": np.float32([8, 8])}
    )
    assert tr.predict_baseline(table, df).tolist() == pytest.approx([0.2, 0.04])


def test_logreg_gebruikt_geen_hoogcardinale_of_verboden_kolommen():
    cols = set(tr.LOGREG_ONEHOT + tr.LOGREG_NUMERIC)
    assert cols <= set(ft.FEATURES)
    assert cols.isdisjoint(ft.FORBIDDEN)
    assert "route_code" not in cols
