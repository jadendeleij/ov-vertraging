"""Tests voor KNMI-weerdownload, -loader en -checks. Geen internet nodig."""

import os
import shutil
import time
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest

from ov_vertraging import download_weather as dw
from ov_vertraging import quality
from ov_vertraging.load_weather import load_weather, parse_header

FIXTURE = Path(__file__).parent / "fixtures" / "knmi-uur-2025-11.txt"


# ---------- download ----------


def test_month_bounds_schrikkeljaar():
    assert dw.month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))


def test_request_params_hele_maand_alle_stations():
    params = dw.request_params("2025-11")
    assert params["start"] == "2025110101"
    assert params["end"] == "2025113024"
    assert params["stns"] == "ALL"
    assert params["vars"].split(":")[0] == "DD"


def _set_mtime(path: Path, day: date) -> None:
    ts = time.mktime(datetime(day.year, day.month, day.day, 12).timetuple())
    os.utime(path, (ts, ts))


def test_is_final_hangt_af_van_downloaddatum(tmp_path):
    f = tmp_path / "knmi-uur-2025-11.txt"
    assert not dw.is_final(f, "2025-11")  # bestaat niet
    f.write_text("x")
    _set_mtime(f, date(2025, 12, 1))  # te vroeg: laatste dagen ontbraken nog
    assert not dw.is_final(f, "2025-11")
    _set_mtime(f, date(2025, 12, 10))
    assert dw.is_final(f, "2025-11")


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        pass


def test_definitieve_maand_wordt_overgeslagen(tmp_path, monkeypatch):
    f = tmp_path / "knmi-uur-2025-11.txt"
    f.write_text("al binnen")
    _set_mtime(f, date(2025, 12, 10))

    def geen_netwerk(*args, **kwargs):
        raise AssertionError("er had geen request gedaan mogen worden")

    monkeypatch.setattr(dw.requests, "post", geen_netwerk)
    assert dw.download_weather_month("2025-11", target_dir=tmp_path) == f
    assert f.read_text() == "al binnen"


def test_onvolledige_maand_wordt_opnieuw_opgehaald(tmp_path, monkeypatch):
    f = tmp_path / "knmi-uur-2025-11.txt"
    f.write_text("oud")
    _set_mtime(f, date(2025, 12, 1))
    monkeypatch.setattr(dw.requests, "post", lambda *a, **k: FakeResponse(FIXTURE.read_text()))

    dw.download_weather_month("2025-11", target_dir=tmp_path)
    assert f.read_text() == FIXTURE.read_text()


def test_foutpagina_geeft_fout_en_schrijft_niets(tmp_path, monkeypatch):
    monkeypatch.setattr(dw.requests, "post", lambda *a, **k: FakeResponse("<html>fout</html>"))
    with pytest.raises(ValueError):
        dw.download_weather_month("2025-11", target_dir=tmp_path)
    assert not list(tmp_path.iterdir())


# ---------- load ----------


def test_parse_header():
    columns, stations, n_header = parse_header(FIXTURE)
    assert columns[:4] == ["STN", "YYYYMMDD", "HH", "T"]
    de_bilt = next(s for s in stations if s[0] == 260)
    assert de_bilt == (260, 5.18, 52.1, 1.9, "De Bilt")
    assert n_header == sum(1 for line in FIXTURE.open() if line.startswith("#"))


@pytest.fixture
def weather_db(tmp_path):
    d = tmp_path / "weather"
    d.mkdir()
    shutil.copy(FIXTURE, d / FIXTURE.name)
    db = tmp_path / "test.duckdb"
    n = load_weather(db_path=db, weather_dir=d)
    return db, d, n


def test_load_filtert_volgende_maand_weg(weather_db):
    db, _, n = weather_db
    assert n == 6  # 7 datarijen in de fixture, waarvan 1 op 1 december
    with duckdb.connect(str(db)) as con:
        assert con.execute("SELECT max(date) FROM raw.weather_hourly").fetchone()[0] == date(
            2025, 11, 30
        )


def test_load_waarden_lege_velden_en_min1(weather_db):
    db, _, _ = weather_db
    with duckdb.connect(str(db)) as con:
        t, fh = con.execute(
            "SELECT t, fh FROM raw.weather_hourly WHERE station_id = 260 AND hour = 1 "
            "AND date = '2025-11-01'"
        ).fetchone()
        assert (t, fh) == (98, 30)  # bron-eenheden: 9,8 °C en 3,0 m/s

        # IJmond (209) meet geen temperatuur: leeg veld wordt NULL.
        assert con.execute(
            "SELECT t FROM raw.weather_hourly WHERE station_id = 209"
        ).fetchone() == (None,)

        # -1 = < 0,05 mm neerslag; raw bewaart de bronwaarde.
        assert con.execute(
            "SELECT rh FROM raw.weather_hourly WHERE station_id = 215"
        ).fetchone() == (-1,)

        n_stations = con.execute("SELECT count(*) FROM raw.weather_stations").fetchone()[0]
        assert n_stations == len(parse_header(FIXTURE)[1])


def test_load_weather_is_idempotent(weather_db):
    db, d, n = weather_db
    assert load_weather(db_path=db, weather_dir=d) == n


def test_load_weather_zonder_bestanden_geeft_fout(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_weather(db_path=tmp_path / "x.duckdb", weather_dir=tmp_path)


# ---------- kwaliteitschecks ----------


def make_con(hours, stations=((260,),)) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA raw")
    con.execute(
        "CREATE TABLE raw.weather_hourly "
        "(station_id INTEGER, date DATE, hour INTEGER, t INTEGER, fx INTEGER)"
    )
    con.execute("CREATE TABLE raw.weather_stations (station_id INTEGER)")
    if hours:
        con.executemany("INSERT INTO raw.weather_hourly VALUES (?, ?, ?, ?, ?)", hours)
    con.executemany("INSERT INTO raw.weather_stations VALUES (?)", list(stations))
    return con


GOED = [(260, "2025-11-01", 1, 98, 50), (260, "2025-11-01", 2, None, None)]


def failed_names(con) -> set[str]:
    return {r.name for r in quality.run_checks(con, quality.WEATHER_CHECKS) if not r.passed}


def test_goede_weerdata_slaagt():
    assert failed_names(make_con(GOED)) == set()


def test_lege_weertabel_faalt():
    assert "weer: aantal rijen > 0" in failed_names(make_con([]))


def test_dubbel_uur_faalt():
    con = make_con([*GOED, (260, "2025-11-01", 1, 98, 50)])
    assert "weer: (station, datum, uur) uniek" in failed_names(con)


def test_uur_nul_faalt():
    con = make_con([*GOED, (260, "2025-11-01", 0, 98, 50)])
    assert "weer: uur tussen 1 en 24" in failed_names(con)


def test_onmogelijke_temperatuur_faalt():
    con = make_con([*GOED, (260, "2025-11-01", 3, 980, 50)])  # 98 °C
    assert "weer: temperatuur en windstoten plausibel" in failed_names(con)


def test_onbekend_weerstation_faalt():
    con = make_con([*GOED, (999, "2025-11-01", 1, 98, 50)])
    assert "weer: elk station heeft coördinaten" in failed_names(con)
