"""Maak dbt-seeds met Nederlandse feestdagen en schoolvakanties.

Gebruik:
    python -m ov_vertraging.calendar_seeds --years 2025 2026

Schrijft twee kleine CSV's naar transform/seeds/ die in git staan, zodat
`dbt build` reproduceerbaar is zonder internet:
- nl_public_holidays.csv: berekend (Pasen via het Gauss/Meeus-algoritme)
- nl_school_holidays.csv: van de open data van de Rijksoverheid, per regio
"""

import argparse
import csv
import logging
from datetime import date, timedelta
from pathlib import Path

import requests

from ov_vertraging.config import PROJECT_ROOT

log = logging.getLogger(__name__)

SEEDS_DIR = PROJECT_ROOT / "transform" / "seeds"
SCHOOL_HOLIDAYS_URL = (
    "https://opendata.rijksoverheid.nl/v1/infotypes/schoolholidays/schoolyear/{schoolyear}"
    "?output=json"
)
REGIONS = ("noord", "midden", "zuid")


def easter_sunday(year: int) -> date:
    """Eerste Paasdag volgens het anonieme Gregoriaanse algoritme (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 (naam uit het algoritme)
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nl_public_holidays(year: int) -> list[tuple[date, str]]:
    """Nederlandse feestdagen (Algemene termijnenwet) in een jaar."""
    easter = easter_sunday(year)
    kings_day = date(year, 4, 27)
    if kings_day.weekday() == 6:  # valt Koningsdag op zondag, dan zaterdag ervoor
        kings_day -= timedelta(days=1)
    holidays = [
        (date(year, 1, 1), "Nieuwjaarsdag"),
        (easter - timedelta(days=2), "Goede Vrijdag"),
        (easter, "Eerste Paasdag"),
        (easter + timedelta(days=1), "Tweede Paasdag"),
        (kings_day, "Koningsdag"),
        (date(year, 5, 5), "Bevrijdingsdag"),
        (easter + timedelta(days=39), "Hemelvaartsdag"),
        (easter + timedelta(days=49), "Eerste Pinksterdag"),
        (easter + timedelta(days=50), "Tweede Pinksterdag"),
        (date(year, 12, 25), "Eerste Kerstdag"),
        (date(year, 12, 26), "Tweede Kerstdag"),
    ]
    return sorted(holidays)


def parse_school_holidays(payload: dict) -> list[tuple[str, str, date, date]]:
    """Zet het Rijksoverheid-JSON om naar (schooljaar, vakantie, regio, start, eind)-rijen.

    "heel Nederland" wordt uitgeschreven naar de drie regio's. De einddatum is in UTC
    opgegeven als 21:59/22:59 = 23:59 Nederlandse tijd, dus de datum zelf klopt.
    """
    rows = []
    for content in payload["content"]:
        schoolyear = content["schoolyear"].strip()
        for vacation in content["vacations"]:
            name = vacation["type"].strip()
            for region in vacation["regions"]:
                start = date.fromisoformat(region["startdate"][:10])
                end = date.fromisoformat(region["enddate"][:10])
                regions = REGIONS if region["region"] == "heel Nederland" else (region["region"],)
                rows.extend((schoolyear, name, r, start, end) for r in regions)
    return rows


def schoolyears_for(years: list[int]) -> list[str]:
    """Alle schooljaren die een kalenderjaar raken: 2026 -> 2025-2026 en 2026-2027."""
    starts = sorted({y - 1 for y in years} | set(years))
    return [f"{y}-{y + 1}" for y in starts]


def write_seeds(years: list[int], seeds_dir: Path = SEEDS_DIR) -> None:
    seeds_dir.mkdir(parents=True, exist_ok=True)

    with (seeds_dir / "nl_public_holidays.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["holiday_date", "holiday_name"])
        for year in years:
            w.writerows((d.isoformat(), name) for d, name in nl_public_holidays(year))

    rows = []
    for schoolyear in schoolyears_for(years):
        resp = requests.get(SCHOOL_HOLIDAYS_URL.format(schoolyear=schoolyear), timeout=30)
        resp.raise_for_status()
        rows.extend(parse_school_holidays(resp.json()))
    with (seeds_dir / "nl_school_holidays.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["schoolyear", "vacation_name", "region", "start_date", "end_date"])
        w.writerows((sy, n, r, s.isoformat(), e.isoformat()) for sy, n, r, s, e in rows)
    log.info("Seeds geschreven naar %s (%d vakantieregels)", seeds_dir, len(rows))


def main() -> None:
    parser = argparse.ArgumentParser(description="Maak dbt-seeds voor feestdagen en vakanties.")
    parser.add_argument("--years", type=int, nargs="+", required=True, help="bv. 2025 2026")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    write_seeds(sorted(args.years))


if __name__ == "__main__":
    main()
