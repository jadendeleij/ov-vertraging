"""Evaluatie: metrieken, uitsplitsingen, kalibratie, SHAP en foutanalyse.

Wordt aangeroepen door train.py. Los opnieuw evalueren van het opgeslagen model:
    python -m ov_vertraging.model.evaluate
"""

import json
import logging
from pathlib import Path

import duckdb
import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")  # geen scherm nodig
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.calibration import calibration_curve  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)

from ov_vertraging.config import DB_PATH, PROJECT_ROOT  # noqa: E402
from ov_vertraging.model import features as ft  # noqa: E402

log = logging.getLogger(__name__)

MODELS_DIR = PROJECT_ROOT / "models"
IMG_DIR = PROJECT_ROOT / "docs" / "img"
COLORS = {"baseline": "#7f7f7f", "logistische regressie": "#1f77b4", "lightgbm": "#d62728"}


def scores(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    """PR-AUC is de hoofdmaat (zeldzame uitkomst); Brier meet hoe goed de kans klopt."""
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "pr_auc": float(average_precision_score(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p)),
        "base_rate": float(np.mean(y)),
        "mean_pred": float(np.mean(p)),
        "n": int(len(y)),
    }


def by_group(df: pd.DataFrame, p: np.ndarray, col: str, min_n: int = 1000) -> pd.DataFrame:
    """Metrieken per groep (bv. treintype of maand); kleine groepen weggelaten."""
    rows = []
    y = df[ft.TARGET].to_numpy()
    for value, idx in df.groupby(col, observed=True).indices.items():
        if len(idx) < min_n or y[idx].min() == y[idx].max():
            continue
        rows.append({col: value, **scores(y[idx], p[idx])})
    return pd.DataFrame(rows).sort_values("n", ascending=False)


def calibration_plot(preds: dict[str, np.ndarray], y: np.ndarray, path: Path) -> None:
    """Voorspelde kans tegen werkelijk percentage, per model (kwantiel-bakjes)."""
    fig, ax = plt.subplots(figsize=(6, 5))
    top = 0.0
    for name, p in preds.items():
        frac, mean_pred = calibration_curve(y, p, n_bins=20, strategy="quantile")
        ax.plot(mean_pred, frac, marker="o", ms=4, label=name, color=COLORS.get(name))
        top = max(top, frac.max(), mean_pred.max())
    top = round(top * 1.15, 2)  # as meeschalen met de data
    ax.plot([0, top], [0, top], "k--", lw=1, label="perfect gekalibreerd")
    ax.set_xlabel("voorspelde kans op ≥ 5 min vertraging")
    ax.set_ylabel("werkelijk % vertraagd")
    ax.set_title("Kalibratie op de testset (sep–okt 2026)")
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def shap_summary(booster: lgb.Booster, X: pd.DataFrame, path: Path) -> pd.DataFrame:
    """SHAP-waarden via LightGBM zelf (pred_contrib), samengevat per feature.

    Geeft de gemiddelde |SHAP| per feature terug en tekent de bekende 'beeswarm'.
    """
    import shap

    contrib = booster.predict(X, pred_contrib=True)[:, :-1]  # laatste kolom = basiswaarde
    importance = (
        pd.DataFrame({"feature": X.columns, "mean_abs_shap": np.abs(contrib).mean(axis=0)})
        .sort_values("mean_abs_shap", ascending=False)
        .reset_index(drop=True)
    )
    # Voor de kleur in de plot: categorische features als codes.
    X_plot = X.copy()
    for col in X_plot.select_dtypes("category"):
        X_plot[col] = X_plot[col].cat.codes
    plt.figure()
    shap.summary_plot(contrib, X_plot, max_display=15, show=False, plot_size=(8, 6))
    plt.title("Welke features duwen de kans omhoog of omlaag? (SHAP, log-odds)")
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return importance


def error_analysis(df: pd.DataFrame, p: np.ndarray, top: int = 5) -> pd.DataFrame:
    """Dagen waarop het model er het verst naast zat: werkelijk vs. gemiddeld voorspeld."""
    daily = (
        pd.DataFrame({"datum": df["departure_date"], "y": df[ft.TARGET], "p": p})
        .groupby("datum")
        .agg(vertrekken=("y", "size"), werkelijk=("y", "mean"), voorspeld=("p", "mean"))
    )
    daily = daily[daily.vertrekken >= 10_000]
    daily["verschil"] = daily.werkelijk - daily.voorspeld
    return daily.sort_values("verschil", ascending=False).head(top).reset_index()


def results_markdown(
    test_scores: dict[str, dict],
    by_type: pd.DataFrame,
    by_month: pd.DataFrame,
    worst_days: pd.DataFrame,
    importance: pd.DataFrame,
) -> str:
    """Resultaten als Markdown, voor docs/model_results.md."""

    def pct(x):
        return f"{100 * x:.1f} %"

    lines = [
        "# Modelresultaten (automatisch gegenereerd door `python -m ov_vertraging.model.train`)",
        "",
        "## Testset (sep–okt 2026)",
        "",
        "| Model | PR-AUC ↑ | ROC-AUC ↑ | Brier ↓ | Gem. voorspeld | Werkelijk |",
        "|---|---|---|---|---|---|",
    ]
    for name, s in test_scores.items():
        lines.append(
            f"| {name} | {s['pr_auc']:.3f} | {s['roc_auc']:.3f} | {s['brier']:.4f} "
            f"| {pct(s['mean_pred'])} | {pct(s['base_rate'])} |"
        )
    lines += [
        "",
        "## LightGBM per treintype",
        "",
        "| Treintype | Vertrekken | % vertraagd | PR-AUC | ROC-AUC |",
        "|---|---|---|---|---|",
    ]
    for _, r in by_type.iterrows():
        lines.append(
            f"| {r.service_type} | {int(r.n):,} | {pct(r.base_rate)} | {r.pr_auc:.3f} "
            f"| {r.roc_auc:.3f} |".replace(",", ".")
        )
    lines += [
        "",
        "## LightGBM per maand",
        "",
        "| Maand | Vertrekken | % vertraagd | PR-AUC | ROC-AUC |",
        "|---|---|---|---|---|",
    ]
    for _, r in by_month.iterrows():
        lines.append(
            f"| {int(r.month)} | {int(r.n):,} | {pct(r.base_rate)} | {r.pr_auc:.3f} "
            f"| {r.roc_auc:.3f} |".replace(",", ".")
        )
    lines += [
        "",
        "## Dagen waarop LightGBM het meest onderschatte",
        "",
        "| Datum | Vertrekken | Werkelijk | Voorspeld |",
        "|---|---|---|---|",
    ]
    for _, r in worst_days.iterrows():
        lines.append(
            f"| {r.datum:%Y-%m-%d} | {int(r.vertrekken):,} | {pct(r.werkelijk)} "
            f"| {pct(r.voorspeld)} |".replace(",", ".")
        )
    lines += [
        "",
        "## Belangrijkste features (gemiddelde |SHAP|)",
        "",
        "| Feature | Gem. \\|SHAP\\| |",
        "|---|---|",
    ]
    for _, r in importance.head(12).iterrows():
        lines.append(f"| `{r.feature}` | {r.mean_abs_shap:.3f} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    """Evalueer het opgeslagen LightGBM-model opnieuw op de testset."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    meta = json.loads((MODELS_DIR / "model_metadata.json").read_text())
    booster = lgb.Booster(model_file=str(MODELS_DIR / meta["model_file"]))
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        test = ft.load_split(con, "test")
    test = ft.align_categories(test, meta["categories"])
    p = booster.predict(test[ft.FEATURES])
    print(json.dumps(scores(test[ft.TARGET].to_numpy(), p), indent=2))


if __name__ == "__main__":
    main()
