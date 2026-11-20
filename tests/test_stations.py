"""Tests voor download, laden en kwaliteitschecks van het stationsbestand."""

from pathlib import Path

import duckdb
import pytest

from ov_vertraging import download, quality
from ov_vertraging.config import STATION_COLUMN_MAP
from ov_vertraging.load import load_stations

FIXTURE = Path(__file__).parent / "fixtures" / "stations-sample.csv"


def test_download_stations_slaat_bestaand_bestand_over(tmp_path, monkeypatch):
    bestaand = tmp_path / "stations-2023-09.csv"
    bestaand.write_text("al gedownload")

    def geen_netwerk(*args, **kwargs):
        raise AssertionError("er had geen request gedaan mogen worden")

    monkeypatch.setattr(download.requests, "get", geen_netwerk)
    assert download.download_stations(target_dir=tmp_path) == bestaand


def test_load_stations_kolommen_en_waarden(tmp_path):
    db = tmp_path / "test.duckdb"
    n_fixture_rows = len(FIXTURE.read_text().splitlines()) - 1

    assert load_stations(db_path=db, stations_file=FIXTURE) == n_fixture_rows

    with duckdb.connect(str(db)) as con:
        cols = [r[0] for r in con.execute("DESCRIBE raw.stations").fetchall()]
        assert cols == [name for name, _ in STATION_COLUMN_MAP.values()] + ["source_file"]

        # Naam met apostrof en een buitenlands station met umlaut.
        ht = con.execute(
            "SELECT name_long, country, station_type, lat FROM raw.stations "
            "WHERE station_code = 'HT'"
        ).fetchone()
        assert ht == ("'s-Hertogenbosch", "NL", "knooppuntIntercitystation", 51.69048)
        assert con.execute(
            "SELECT country FROM raw.stations WHERE name_long = 'Münster (Westf) Hbf'"
        ).fetchone() == ("D",)


def test_load_stations_is_idempotent(tmp_path):
    db = tmp_path / "test.duckdb"
    assert load_stations(db_path=db, stations_file=FIXTURE) == load_stations(
        db_path=db, stations_file=FIXTURE
    )


def test_load_stations_zonder_bestand_geeft_fout(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_stations(db_path=tmp_path / "x.duckdb", stations_file=tmp_path / "ontbreekt.csv")


# ---------- kwaliteitschecks ----------


def make_con(stations, service_codes) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA raw")
    con.execute("CREATE TABLE raw.stations (station_code VARCHAR, lat DOUBLE, lng DOUBLE)")
    con.execute("CREATE TABLE raw.services (station_code VARCHAR)")
    if stations:
        con.executemany("INSERT INTO raw.stations VALUES (?, ?, ?)", stations)
    con.executemany("INSERT INTO raw.services VALUES (?)", [(c,) for c in service_codes])
    return con


GOEDE_STATIONS = [("UT", 52.09, 5.11), ("ASD", 52.38, 4.90)]


def failed_names(con) -> set[str]:
    return {r.name for r in quality.run_checks(con, quality.STATION_CHECKS) if not r.passed}


def test_goede_stations_slagen():
    assert failed_names(make_con(GOEDE_STATIONS, ["UT", "ASD", "UT"])) == set()


def test_lege_stations_faalt():
    assert "stations: aantal rijen > 0" in failed_names(make_con([], ["UT"]))


def test_dubbele_stationscode_faalt():
    con = make_con([*GOEDE_STATIONS, ("UT", 52.09, 5.11)], ["UT"])
    assert "stations: station_code uniek" in failed_names(con)


def test_omgedraaide_coordinaten_falen():
    con = make_con([("UT", 5.11, 52.09)], ["UT"])
    assert "stations: coördinaten in Europa" in failed_names(con)


def test_lage_dekking_faalt():
    con = make_con(GOEDE_STATIONS, ["UT", "XYZ"])  # 50 % dekking
    assert "stops met bekend station ≥ 99%" in failed_names(con)
