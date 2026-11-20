"""Tests voor de dashboard-queries op een klein nepschema (geen echte data nodig)."""

from datetime import date

import duckdb
import pytest

from ov_vertraging import dashboard_queries as dq

ALLES = dq.Filters(date(2026, 1, 1), date(2026, 1, 31), ("Intercity", "Sprinter"))


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE SCHEMA marts; CREATE SCHEMA staging")
    c.execute(
        """
        CREATE TABLE marts.dim_station AS SELECT * FROM (VALUES
            ('BGN', 'Bergen op Zoom', true, 51.49, 4.29),
            ('RTB', 'Rotterdam Blaak', true, 51.92, 4.49),
            ('EMR', 'Emmerich', false, 51.83, 6.25)
        ) AS t(station_code, station_name, is_dutch, lat, lng)
        """
    )
    # 4 vertrekken vanaf BGN: 1 vertraagd, 1 geannuleerd, 2 op tijd; 1 Sprinter elders;
    # 1 vertrek in februari (buiten de periode van de filters).
    c.execute(
        """
        CREATE TABLE marts.fct_departures AS SELECT * FROM (VALUES
            (DATE '2026-01-05', 'Intercity', 'BGN', 8, 1, 7,    false, true,  -1.0, 7, 9),
            (DATE '2026-01-05', 'Intercity', 'BGN', 8, 1, 0,    false, false,  5.0, 1, 3),
            (DATE '2026-01-06', 'Intercity', 'BGN', 9, 2, NULL, true,  NULL,   5.0, 1, 3),
            (DATE '2026-01-06', 'Intercity', 'BGN', 9, 2, 1,    false, false,  5.0, 1, 3),
            (DATE '2026-01-06', 'Sprinter',  'RTB', 9, 2, 0,    false, false, 31.0, 1, 3),
            (DATE '2026-02-01', 'Intercity', 'BGN', 9, 7, 30,   false, true,   5.0, 1, 3)
        ) AS t(departure_date, service_type, station_code, departure_hour, departure_weekday,
               departure_delay_min, is_cancelled, is_delayed_5min, temperature_c,
               wind_bft, wind_gust_bft)
        """
    )
    c.execute(
        """
        ALTER TABLE marts.fct_departures ADD COLUMN is_frost BOOLEAN DEFAULT false;
        ALTER TABLE marts.fct_departures ADD COLUMN is_heavy_rain BOOLEAN DEFAULT false;
        ALTER TABLE marts.fct_departures ADD COLUMN has_snow BOOLEAN DEFAULT false;
        """
    )
    # Twee directe ritten BGN -> RTB: één op tijd, één 6 min te laat aangekomen.
    # Rit 3 rijdt de andere kant op en mag niet meetellen.
    c.execute(
        """
        CREATE TABLE stops AS SELECT * FROM (VALUES
            (1, 'BGN', NULL, TIMESTAMPTZ '2026-01-05 08:10+01', 8, 0, NULL),
            (1, 'RTB', TIMESTAMPTZ '2026-01-05 09:00+01', NULL, NULL, NULL, 0),
            (2, 'BGN', NULL, TIMESTAMPTZ '2026-01-05 10:10+01', 10, 2, NULL),
            (2, 'RTB', TIMESTAMPTZ '2026-01-05 11:00+01', NULL, NULL, NULL, 6),
            (3, 'RTB', NULL, TIMESTAMPTZ '2026-01-05 12:00+01', 12, 0, NULL),
            (3, 'BGN', TIMESTAMPTZ '2026-01-05 12:50+01', NULL, NULL, NULL, 0)
        ) AS t(service_id, station_code, arrival_time, departure_time, departure_hour,
               departure_delay_min, arrival_delay_min);

        CREATE VIEW staging.stg_services AS
        SELECT *, 'Intercity' AS service_type, 1 AS departure_weekday,
               false AS departure_cancelled, false AS arrival_cancelled,
               true AS is_rail_service,
               timezone('Europe/Amsterdam', departure_time) AS departure_time_local
        FROM stops;
        """
    )
    return c


def test_where_met_parameters():
    sql, params = dq.where(ALLES, "f")
    assert sql == "f.departure_date BETWEEN ? AND ? AND f.service_type IN (?, ?)"
    assert params == [date(2026, 1, 1), date(2026, 1, 31), "Intercity", "Sprinter"]


def test_where_zonder_treintype_geeft_niets(con):
    leeg = dq.Filters(date(2026, 1, 1), date(2026, 1, 31), ())
    assert dq.where(leeg) == ("false", [])
    assert dq.kpis(con, leeg)["vertrekken"] == 0


def test_kpis(con):
    k = dq.kpis(con, ALLES)
    assert k["vertrekken"] == 5  # februari valt buiten de periode
    assert k["pct_vertraagd"] == pytest.approx(100 * 1 / 4)  # geannuleerd telt niet mee
    assert k["pct_uitval"] == pytest.approx(100 * 1 / 5)


def test_filter_op_treintype(con):
    alleen_sprinter = dq.Filters(ALLES.start, ALLES.end, ("Sprinter",))
    assert dq.kpis(con, alleen_sprinter)["vertrekken"] == 1


def test_stations_alleen_met_genoeg_vertrekken(con):
    df = dq.stations(con, ALLES, min_departures=2)
    assert list(df.station_code) == ["BGN"]
    assert df.vertrekken.iloc[0] == 4


def test_stations_buitenland_optioneel(con):
    con.execute(
        "INSERT INTO marts.fct_departures (departure_date, service_type, station_code, "
        "is_cancelled, is_delayed_5min) VALUES ('2026-01-05', 'Sprinter', 'EMR', false, true)"
    )
    nl = dq.stations(con, ALLES, min_departures=1)
    alles = dq.stations(con, ALLES, min_departures=1, dutch_only=False)
    assert "EMR" not in set(nl.station_code)
    assert "EMR" in set(alles.station_code)


def test_weather_effects(con):
    df = dq.weather_effects(con, ALLES).set_index("weer")
    assert df.loc["Normaal (alles)", "vertrekken"] == 4
    assert df.loc["Windstoten ≥ 9 Bft", "pct"] == pytest.approx(100.0)
    assert df.loc["Hitte (≥ 30 °C)", "vertrekken"] == 1


def test_station_options_alleen_nederland(con):
    assert list(dq.station_options(con).station_code) == ["BGN", "RTB"]


def test_trip_summary_alleen_juiste_richting(con):
    df = dq.trip_summary(con, "BGN", "RTB", ALLES)
    assert df.ritten.iloc[0] == 2
    assert df.pct_aankomst_5min_laat.iloc[0] == pytest.approx(50.0)
    assert df.reistijd_min.iloc[0] == 50


def test_trip_by_hour(con):
    df = dq.trip_by_hour(con, "BGN", "RTB", ALLES)
    assert list(df.uur) == [8, 10]
