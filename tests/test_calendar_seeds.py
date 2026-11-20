"""Tests voor feestdagen en schoolvakanties (geen internet)."""

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from ov_vertraging import calendar_seeds as cs

FIXTURE = Path(__file__).parent / "fixtures" / "schoolvakanties-2025-2026.json"


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2038, date(2038, 4, 25)),
    ],
)
def test_easter_sunday(year, expected):
    assert cs.easter_sunday(year) == expected


def test_koningsdag_op_zondag_wordt_zaterdag():
    holidays = dict((name, d) for d, name in cs.nl_public_holidays(2025))
    assert holidays["Koningsdag"] == date(2025, 4, 26)  # 27-04-2025 was een zondag
    holidays_2026 = dict((name, d) for d, name in cs.nl_public_holidays(2026))
    assert holidays_2026["Koningsdag"] == date(2026, 4, 27)


def test_feestdagen_2026():
    holidays = dict((name, d) for d, name in cs.nl_public_holidays(2026))
    assert holidays["Hemelvaartsdag"] == date(2026, 5, 14)
    assert holidays["Tweede Pinksterdag"] == date(2026, 5, 25)
    assert len(holidays) == 11


def test_parse_school_holidays_regios_en_heel_nederland():
    rows = cs.parse_school_holidays(json.loads(FIXTURE.read_text()))
    herfst_zuid = [r for r in rows if r[1] == "Herfstvakantie" and r[2] == "zuid"]
    assert herfst_zuid == [
        ("2025-2026", "Herfstvakantie", "zuid", date(2025, 10, 11), date(2025, 10, 19))
    ]
    kerst = [r for r in rows if r[1] == "Kerstvakantie"]
    assert {r[2] for r in kerst} == {"noord", "midden", "zuid"}  # heel Nederland uitgeschreven
    assert all(r[4] == date(2026, 1, 4) for r in kerst)


def test_schoolyears_for():
    assert cs.schoolyears_for([2025, 2026]) == ["2024-2025", "2025-2026", "2026-2027"]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_write_seeds(tmp_path, monkeypatch):
    payload = json.loads(FIXTURE.read_text())
    monkeypatch.setattr(cs.requests, "get", lambda *a, **k: FakeResponse(payload))
    cs.write_seeds([2026], seeds_dir=tmp_path)

    with (tmp_path / "nl_public_holidays.csv").open() as f:
        assert len(list(csv.DictReader(f))) == 11
    with (tmp_path / "nl_school_holidays.csv").open() as f:
        rows = list(csv.DictReader(f))
    # 2 schooljaren opgevraagd, fixture heeft per jaar 3 herfst- + 3 kerstregels
    assert len(rows) == 12
    assert rows[0].keys() == {"schoolyear", "vacation_name", "region", "start_date", "end_date"}
