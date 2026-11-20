"""Download KNMI-uurwaarden van alle weerstations, één bestand per maand.

Gebruik:
    python -m ov_vertraging.download_weather --start 2025-11 --end 2026-10

Idempotent: een maand die al definitief is gedownload wordt overgeslagen. Een
maand die nog niet compleet was (de gevalideerde data loopt ~2 dagen achter)
wordt bij een volgende run opnieuw opgehaald, zodat de laatste dagen erbij komen.

Bestanden worden ongewijzigd opgeslagen (raw = precies wat de bron gaf). Let op:
KNMI geeft bij een einde op uur 24 ook de volgende dag mee; de loader filtert
daarom per bestand op de eigen maand.
"""

import argparse
import calendar
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from ov_vertraging.config import (
    WEATHER_DIR,
    WEATHER_FILENAME,
    WEATHER_LAG_DAYS,
    WEATHER_URL,
    WEATHER_VARS,
)
from ov_vertraging.download import month_range

log = logging.getLogger(__name__)

HEADER_MARKER = "# STN,YYYYMMDD,HH"


def month_bounds(month: str) -> tuple[date, date]:
    """Eerste en laatste dag van een maand 'YYYY-MM'."""
    y, m = map(int, month.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def is_final(path: Path, month: str, lag_days: int = WEATHER_LAG_DAYS) -> bool:
    """Is dit bestand gedownload nadat de maand compleet beschikbaar was?"""
    if not path.exists():
        return False
    downloaded_on = datetime.fromtimestamp(path.stat().st_mtime).date()
    return downloaded_on >= month_bounds(month)[1] + timedelta(days=lag_days)


def request_params(month: str) -> dict[str, str]:
    first, last = month_bounds(month)
    return {
        "start": f"{first:%Y%m%d}01",
        "end": f"{last:%Y%m%d}24",
        "vars": ":".join(WEATHER_VARS),
        "stns": "ALL",
    }


def download_weather_month(month: str, target_dir: Path = WEATHER_DIR) -> Path:
    """Download één maand weerdata; sla over als die al definitief binnen is."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / WEATHER_FILENAME.format(month=month)
    if is_final(target, month):
        log.info("Al definitief, overgeslagen: %s", target.name)
        return target

    log.info("Downloaden: KNMI uurgegevens %s", month)
    resp = requests.post(WEATHER_URL, data=request_params(month), timeout=120)
    resp.raise_for_status()
    if HEADER_MARKER not in resp.text:
        # Bij fouten geeft KNMI soms een HTML- of foutpagina met status 200.
        raise ValueError(f"Onverwacht antwoord van KNMI voor {month}: {resp.text[:200]!r}")

    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_text(resp.text, encoding="utf-8")
    tmp.rename(target)
    log.info("Klaar: %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Download KNMI-uurgegevens per maand.")
    parser.add_argument("--start", required=True, help="Eerste maand, bv. 2025-11")
    parser.add_argument("--end", required=True, help="Laatste maand, bv. 2026-10")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for month in month_range(args.start, args.end):
        download_weather_month(month)


if __name__ == "__main__":
    main()
