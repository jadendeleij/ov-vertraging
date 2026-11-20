"""Tests voor de datakwaliteitschecks, met kleine tabellen die in de test worden gemaakt."""

import duckdb
import pytest

from ov_vertraging import quality

GOED = [
    # service_id, stop_id, service_date, station_code, departure_delay_min
    (1, 10, "2026-10-01", "UT", 0),
    (1, 11, "2026-10-01", "ASD", None),  # eindstation: geen vertrek
    (2, 20, "2026-10-01", "RTD", 3),
    (2, 21, "2026-10-01", "GD", None),
]


def make_con(rows) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA raw")
    con.execute(
        """
        CREATE TABLE raw.services (
            service_id BIGINT, stop_id BIGINT, service_date DATE,
            station_code VARCHAR, departure_delay_min INTEGER
        )
        """
    )
    if rows:
        con.executemany("INSERT INTO raw.services VALUES (?, ?, ?, ?, ?)", rows)
    return con


def failed_names(con) -> set[str]:
    return {r.name for r in quality.run_checks(con) if not r.passed}


def test_goede_data_slaagt_voor_alle_checks():
    con = make_con(GOED)
    assert failed_names(con) == set()
    quality.assert_quality(con)  # geen exceptie


def test_lege_tabel_faalt():
    assert "aantal rijen > 0" in failed_names(make_con([]))


def test_duplicaat_stop_id_faalt():
    rows = [*GOED, (2, 20, "2026-10-01", "RTD", 3)]
    assert "stop_id uniek" in failed_names(make_con(rows))


def test_null_in_sleutelkolom_faalt():
    rows = [*GOED[:3], (2, 21, "2026-10-01", None, None)]
    assert "sleutelkolommen niet NULL" in failed_names(make_con(rows))


def test_rit_met_een_stop_faalt():
    rows = [*GOED, (3, 30, "2026-10-01", "HT", 0)]
    assert "elke rit ≥ 2 stops" in failed_names(make_con(rows))


def test_veel_extreme_vertraging_faalt():
    rows = [*GOED[:3], (2, 21, "2026-10-01", "GD", 1440)]  # 1 op 3 vertrekken
    con = make_con(rows)
    assert "departure_delay_min in [-5, 600]" in failed_names(con)
    with pytest.raises(quality.DataQualityError):
        quality.assert_quality(con)


def test_klein_aandeel_extreme_vertraging_wordt_getolereerd(monkeypatch):
    monkeypatch.setattr(quality, "MAX_OUT_OF_RANGE_FRACTION", 0.5)
    rows = [*GOED[:3], (2, 21, "2026-10-01", "GD", 1440)]
    assert failed_names(make_con(rows)) == set()


def test_null_percentages():
    pct = quality.null_percentages(make_con(GOED))
    assert pct["departure_delay_min"] == 50.0
    assert pct["stop_id"] == 0.0
