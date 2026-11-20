"""Download maandbestanden van het Rijden de Treinen treinarchief en het stationsbestand.

Gebruik:
    python -m ov_vertraging.download --start 2026-10 --end 2026-10
    python -m ov_vertraging.download --stations

Idempotent: een bestand dat al bestaat wordt overgeslagen. We schrijven eerst naar
een .part-bestand en hernoemen pas als de download compleet is, zodat een
afgebroken download nooit voor een geldig bestand wordt aangezien.
"""

import argparse
import logging
from datetime import date
from pathlib import Path

import requests

from ov_vertraging.config import (
    SERVICES_DIR,
    SERVICES_FILENAME,
    SERVICES_URL,
    STATIONS_DIR,
    STATIONS_FILENAME,
    STATIONS_URL,
)

log = logging.getLogger(__name__)


def month_range(start: str, end: str) -> list[str]:
    """Geef alle maanden van start t/m end als 'YYYY-MM'-strings."""
    y, m = map(int, start.split("-"))
    end_y, end_m = map(int, end.split("-"))
    first, last = date(y, m, 1), date(end_y, end_m, 1)
    if first > last:
        raise ValueError(f"start ({start}) ligt na end ({end})")

    months = []
    while (y, m) <= (end_y, end_m):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def download_month(month: str, target_dir: Path = SERVICES_DIR) -> Path:
    """Download één maand van het treinarchief."""
    target = target_dir / SERVICES_FILENAME.format(month=month)
    return download_file(SERVICES_URL.format(month=month), target)


def download_stations(target_dir: Path = STATIONS_DIR) -> Path:
    """Download het stationsbestand."""
    return download_file(STATIONS_URL, target_dir / STATIONS_FILENAME)


def download_file(url: str, target: Path) -> Path:
    """Download url naar target; sla over als het bestand al bestaat."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        log.info("Bestaat al, overgeslagen: %s", target.name)
        return target

    tmp = target.with_suffix(target.suffix + ".part")
    log.info("Downloaden: %s", url)
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.rename(target)
    log.info("Klaar: %s (%.1f MB)", target.name, target.stat().st_size / 1e6)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Rijden de Treinen open data.")
    parser.add_argument("--start", help="Eerste maand treinarchief, bv. 2025-11")
    parser.add_argument("--end", help="Laatste maand treinarchief, bv. 2026-10")
    parser.add_argument("--stations", action="store_true", help="Download ook het stationsbestand")
    args = parser.parse_args()
    if bool(args.start) != bool(args.end):
        parser.error("--start en --end horen bij elkaar")
    if not (args.start or args.stations):
        parser.error("geef --start/--end en/of --stations")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.stations:
        download_stations()
    if args.start:
        for month in month_range(args.start, args.end):
            download_month(month)


if __name__ == "__main__":
    main()
