"""Centrale instellingen: paden, URL's en het kolomschema van het treinarchief."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SERVICES_DIR = RAW_DIR / "services"
STATIONS_DIR = RAW_DIR / "stations"
DB_PATH = DATA_DIR / "ov.duckdb"

# Rijden de Treinen treinarchief (CC BY 4.0)
SERVICES_URL = "https://opendata.rijdendetreinen.nl/public/services/services-{month}.csv.gz"
SERVICES_FILENAME = "services-{month}.csv.gz"

# Rijden de Treinen stationsbestand (CC BY 4.0). 2023-09 is de nieuwste versie;
# de variant zonder "-nl" bevat ook buitenlandse stations (591 i.p.v. 397), nodig
# omdat het treinarchief ook ritten naar Duitsland/België bevat.
STATIONS_VERSION = "2023-09"
STATIONS_FILENAME = f"stations-{STATIONS_VERSION}.csv"
STATIONS_URL = f"https://opendata.rijdendetreinen.nl/public/stations/{STATIONS_FILENAME}"

# Originele kolomnaam -> (nette naam in DuckDB, DuckDB-type).
STATION_COLUMN_MAP: dict[str, tuple[str, str]] = {
    "id": ("rdt_station_id", "INTEGER"),
    "code": ("station_code", "VARCHAR"),
    "uic": ("uic_code", "BIGINT"),
    "name_short": ("name_short", "VARCHAR"),
    "name_medium": ("name_medium", "VARCHAR"),
    "name_long": ("name_long", "VARCHAR"),
    "slug": ("slug", "VARCHAR"),
    "country": ("country", "VARCHAR"),
    "type": ("station_type", "VARCHAR"),
    "geo_lat": ("lat", "DOUBLE"),
    "geo_lng": ("lng", "DOUBLE"),
}

# Alle tijden in DuckDB tonen we in Nederlandse tijd.
TIMEZONE = "Europe/Amsterdam"

# Originele kolomnaam in de CSV -> (nette naam in DuckDB, DuckDB-type).
# Gecontroleerd tegen het echte bestand services-2026-10.csv.gz (header gelijk aan de docs).
COLUMN_MAP: dict[str, tuple[str, str]] = {
    "Service:RDT-ID": ("service_id", "BIGINT"),
    "Service:Date": ("service_date", "DATE"),
    "Service:Type": ("service_type", "VARCHAR"),
    "Service:Company": ("company", "VARCHAR"),
    "Service:Train number": ("train_number", "INTEGER"),
    "Service:Completely cancelled": ("service_completely_cancelled", "BOOLEAN"),
    "Service:Partly cancelled": ("service_partly_cancelled", "BOOLEAN"),
    "Service:Maximum delay": ("service_max_delay_min", "INTEGER"),
    "Stop:RDT-ID": ("stop_id", "BIGINT"),
    "Stop:Station code": ("station_code", "VARCHAR"),
    "Stop:Station name": ("station_name", "VARCHAR"),
    "Stop:Arrival time": ("arrival_time", "TIMESTAMPTZ"),
    "Stop:Arrival delay": ("arrival_delay_min", "INTEGER"),
    "Stop:Arrival cancelled": ("arrival_cancelled", "BOOLEAN"),
    "Stop:Departure time": ("departure_time", "TIMESTAMPTZ"),
    "Stop:Departure delay": ("departure_delay_min", "INTEGER"),
    "Stop:Departure cancelled": ("departure_cancelled", "BOOLEAN"),
    "Stop:Platform change": ("platform_change", "BOOLEAN"),
    # Perrons zijn tekst: "15a", "1/2", ...
    "Stop:Planned platform": ("planned_platform", "VARCHAR"),
    "Stop:Actual platform": ("actual_platform", "VARCHAR"),
}

# Handig voor wie alleen de types per CSV-kolom nodig heeft (bv. read_csv).
TYPES: dict[str, str] = {src: dtype for src, (_, dtype) in COLUMN_MAP.items()}
