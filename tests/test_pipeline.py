"""Tests voor download en loader. Geen internet, geen echte grote data."""

import gzip
import shutil
from pathlib import Path

import duckdb
import pytest

from ov_vertraging import download
from ov_vertraging.config import COLUMN_MAP
from ov_vertraging.load import load_services

FIXTURE = Path(__file__).parent / "fixtures" / "services-sample.csv"


# ---------- download ----------


def test_month_range_over_jaargrens():
    assert download.month_range("2025-11", "2026-02") == [
        "2025-11",
        "2025-12",
        "2026-01",
        "2026-02",
    ]


def test_month_range_een_maand():
    assert download.month_range("2026-10", "2026-10") == ["2026-10"]


def test_month_range_omgekeerd_geeft_fout():
    with pytest.raises(ValueError):
        download.month_range("2026-10", "2026-09")


def test_download_slaat_bestaand_bestand_over(tmp_path, monkeypatch):
    bestaand = tmp_path / "services-2026-10.csv.gz"
    bestaand.write_bytes(b"al gedownload")

    def geen_netwerk(*args, **kwargs):
        raise AssertionError("er had geen request gedaan mogen worden")

    monkeypatch.setattr(download.requests, "get", geen_netwerk)
    assert download.download_month("2026-10", target_dir=tmp_path) == bestaand
    assert bestaand.read_bytes() == b"al gedownload"


# ---------- load ----------


@pytest.fixture
def services_dir(tmp_path):
    """Map met de echte voorbeeldregels als gzip, zoals de download ze aanlevert."""
    d = tmp_path / "services"
    d.mkdir()
    with FIXTURE.open("rb") as src, gzip.open(d / "services-2026-10.csv.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    return d


def test_load_rijen_kolommen_en_types(tmp_path, services_dir):
    db = tmp_path / "test.duckdb"
    n_fixture_rows = len(FIXTURE.read_text().splitlines()) - 1  # zonder header

    assert load_services(db_path=db, services_dir=services_dir) == n_fixture_rows

    with duckdb.connect(str(db)) as con:
        cols = [r[0] for r in con.execute("DESCRIBE raw.services").fetchall()]
        assert cols == [name for name, _ in COLUMN_MAP.values()] + ["source_file"]

        # Perron "15a" moet tekst blijven, booleans echte booleans, tijden met tijdzone.
        row = con.execute(
            """
            SELECT planned_platform, platform_change, departure_delay_min,
                   epoch(departure_time), source_file
            FROM raw.services WHERE stop_id = 175297626
            """
        ).fetchone()
        # 2026-10-01T02:46:00+02:00 == 2026-10-01T00:46:00Z
        assert row == ("15a", False, 6, 1790815560.0, "services-2026-10.csv.gz")

        # Lege velden (geen aankomst op beginstation) worden NULL.
        first_stop = con.execute(
            "SELECT arrival_time, arrival_delay_min FROM raw.services WHERE stop_id = 175297625"
        ).fetchone()
        assert first_stop == (None, None)


def test_load_is_idempotent(tmp_path, services_dir):
    db = tmp_path / "test.duckdb"
    first = load_services(db_path=db, services_dir=services_dir)
    second = load_services(db_path=db, services_dir=services_dir)
    assert first == second


def test_load_zonder_bestanden_geeft_fout(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_services(db_path=tmp_path / "x.duckdb", services_dir=tmp_path)
