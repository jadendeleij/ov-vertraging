"""Centrale instellingen: paden, URL's en kolomschema's van treinarchief, stations en weer."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
SERVICES_DIR = RAW_DIR / "services"
STATIONS_DIR = RAW_DIR / "stations"
# Overschrijfbaar (zelfde variabele als het dbt-profiel), bv. voor CI of het dashboard
# zonder database testen.
DB_PATH = Path(os.getenv("OV_DUCKDB_PATH", DATA_DIR / "ov.duckdb"))

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

# KNMI uurgegevens (gevalideerde uurwaarden van alle automatische weerstations).
# Gekozen boven het KNMI Data Platform: geen API-key, één request per maand voor
# alle stations, CSV met stationscoördinaten in de kop. Zie README.
WEATHER_DIR = RAW_DIR / "weather"
WEATHER_URL = "https://www.daggegevens.knmi.nl/klimatologie/uurgegevens"
WEATHER_FILENAME = "knmi-uur-{month}.txt"
# Validated data loopt ~2 dagen achter; een maand is pas "definitief" als het
# bestand minstens zoveel dagen na het einde van de maand is gedownload.
WEATHER_LAG_DAYS = 3

# KNMI-code -> omschrijving. In raw bewaren we de bronwaarden (veel in tienden);
# omrekenen naar °C, mm en m/s gebeurt in de dbt-staginglaag.
# Niet opgenomen: WW (weercode) — deze dienst levert die voor geen enkel station.
WEATHER_VARS: dict[str, str] = {
    "DD": "windrichting (graden; 0 = windstil, 990 = veranderlijk)",
    "FH": "uurgemiddelde windsnelheid (0,1 m/s)",
    "FX": "hoogste windstoot (0,1 m/s)",
    "T": "temperatuur op 1,5 m (0,1 °C)",
    "SQ": "zonneschijnduur (0,1 uur; -1 = < 0,05 uur)",
    "RH": "neerslag uursom (0,1 mm; -1 = < 0,05 mm)",
    "VV": "zicht (code: 0 = < 100 m ... 89 = > 70 km)",
    "N": "bewolking (achtsten; 9 = bovenlucht onzichtbaar)",
    "U": "relatieve vochtigheid (%)",
    "M": "mist (0/1)",
    "S": "sneeuw (0/1)",
    "O": "onweer (0/1)",
    "Y": "ijsvorming (0/1)",
}

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
