"""Tests voor de featureset: geen leakage, alleen verleden in de historische features."""

from datetime import date

import duckdb
import pytest

from ov_vertraging.model import features as ft


def test_geen_verboden_kolommen_in_featureset():
    assert set(ft.FEATURES).isdisjoint(ft.FORBIDDEN)
    assert ft.TARGET not in ft.FEATURES


def test_features_uniek_en_categorisch_of_numeriek():
    assert len(ft.FEATURES) == len(set(ft.FEATURES))
    assert set(ft.CATEGORICAL).isdisjoint(ft.NUMERIC)


def test_splits_op_tijd_zonder_overlap():
    periods = [ft.SPLITS[s] for s in ("train", "validation", "test")]
    for earlier, later in zip(periods, periods[1:], strict=False):
        assert earlier.end < later.start


@pytest.fixture
def con():
    """Mini-warehouse: één traject A-B om 08:00, trein 100, drie dagen.
    Dag 1 op tijd, dag 2 vertraagd, dag 3 op tijd."""
    c = duckdb.connect()
    c.execute("CREATE SCHEMA marts")
    c.execute(
        """
        CREATE TABLE marts.dim_station AS
        SELECT * FROM (VALUES ('A', true), ('B', true)) AS t(station_code, is_dutch)
        """
    )
    c.execute(
        """
        CREATE TABLE marts.dim_date AS
        SELECT d::DATE AS date_day, false AS is_public_holiday, 0 AS n_regions_school_holiday
        FROM range(DATE '2026-01-01', DATE '2026-01-04', INTERVAL 1 DAY) AS t(d)
        """
    )
    c.execute(
        """
        CREATE TABLE marts.fct_departures AS
        SELECT
            i AS departure_id,
            (DATE '2026-01-01' + INTERVAL (i - 1) DAY)::DATE AS departure_date,
            i = 2 AS is_delayed_5min,
            'Intercity' AS service_type, 'NS' AS company,
            'A' AS station_code, 'B' AS next_station_code, 'A-B' AS route_code,
            8 AS departure_hour, 4 AS departure_weekday,
            1 AS stop_sequence, true AS is_first_stop, 100 AS train_number,
            5.0 AS temperature_c, 0.0 AS precipitation_mm, 3.0 AS wind_speed_ms,
            6.0 AS wind_gust_ms, 9000 AS visibility_m, 80 AS humidity_pct,
            false AS has_snow, false AS has_fog, false AS has_ice,
            -- verboden kolommen bestaan wel in de bron, maar mogen niet doorlekken
            CASE WHEN i = 2 THEN 12 ELSE 0 END AS departure_delay_min,
            false AS is_cancelled
        FROM range(1, 4) AS t(i)
        """
    )
    ft.build_feature_table(c)
    return c


def _feature(con, day: int, col: str) -> float:
    return con.execute(
        f"SELECT {col} FROM {ft.TABLE} WHERE departure_date = ?",
        [date(2026, 1, day)],
    ).fetchone()[0]


def test_tabel_bevat_precies_de_features_plus_sleutels(con):
    cols = [r[0] for r in con.execute(f"DESCRIBE {ft.TABLE}").fetchall()]
    assert set(cols) == {"departure_id", "departure_date", ft.TARGET, *ft.FEATURES}
    assert set(cols).isdisjoint(ft.FORBIDDEN - {ft.TARGET})


def test_historie_gebruikt_alleen_eerdere_dagen(con):
    # Dag 1: nog geen geschiedenis.
    assert _feature(con, 1, "hist_route_hour_n") == 0
    # Dag 2 is zelf vertraagd, maar ziet alleen dag 1 (op tijd): 0 % historisch.
    assert _feature(con, 2, "hist_route_hour_n") == 1
    assert _feature(con, 2, "hist_route_hour_pct") == pytest.approx(0.0)
    assert _feature(con, 2, "hist_train_pct") == pytest.approx(0.0)
    # Dag 3 ziet dag 1 en 2: 1 van de 2 vertraagd.
    assert _feature(con, 3, "hist_train_n") == 2


def test_smoothing_naar_historisch_gemiddelde(con):
    # Dag 1: geen geschiedenis -> volledig de startwaarde 0,04.
    assert _feature(con, 1, "hist_route_hour_pct") == pytest.approx(0.04)
    # Dag 3: (1 vertraagd + 50 x landelijk 1/2) / (2 + 50) = 26/52.
    assert _feature(con, 3, "hist_route_hour_pct") == pytest.approx(26 / 52)


def test_drukte_uit_dienstregeling(con):
    assert _feature(con, 1, "planned_station_hour") == 1
    assert _feature(con, 1, "planned_network_hour") == 1


def test_load_split_types(con, monkeypatch):
    monkeypatch.setitem(ft.SPLITS, "train", ft.Period(date(2026, 1, 1), date(2026, 1, 2)))
    df = ft.load_split(con, "train")
    assert len(df) == 2
    assert str(df["route_code"].dtype) == "category"
    assert str(df["hist_route_hour_pct"].dtype) == "float32"
    assert df[ft.TARGET].tolist() == [0, 1]


def test_steekproef_binnen_de_periode_met_precies_n_rijen(con, monkeypatch):
    # Periode = dag 2 en 3; de steekproef moet ná het filteren genomen worden.
    monkeypatch.setitem(ft.SPLITS, "train", ft.Period(date(2026, 1, 2), date(2026, 1, 3)))
    df = ft.load_split(con, "train", sample_n=2)
    assert len(df) == 2
    assert df.departure_date.min().date() >= date(2026, 1, 2)
