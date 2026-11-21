"""Weersverwachting van Open-Meteo (KNMI HARMONIE-model), vertaald naar modelfeatures.

Gratis, geen API-key; voorwaarden: niet-commercieel, < 10.000 verzoeken per dag.
https://open-meteo.com/en/docs/knmi-api

Het model is getraind op KNMI-*metingen*; hier gebruiken we een *verwachting* van
hetzelfde KNMI-model. Mist/sneeuw/ijzel zijn bij KNMI waarnemingen; hier leiden we ze
af uit de WMO-weercode, wat een benadering is (zie docs/model.md).
"""

import logging
import time
from datetime import date

import requests

log = logging.getLogger(__name__)

URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = [
    "temperature_2m",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "visibility",
    "relative_humidity_2m",
    "snowfall",
    "weather_code",
]
# WMO-weercodes: https://open-meteo.com/en/docs (onderaan)
FOG_CODES = {45, 48}
ICE_CODES = {56, 57, 66, 67}  # (motregen/regen) die aanvriest
SNOW_CODES = {71, 73, 75, 77, 85, 86}

WEATHER_FEATURES = [
    "temperature_c",
    "precipitation_mm",
    "wind_speed_ms",
    "wind_gust_ms",
    "visibility_m",
    "humidity_pct",
    "has_snow",
    "has_fog",
    "has_ice",
]


def to_features(hour: dict) -> dict:
    """Eén uur Open-Meteo → de weerfeatures van het model (zelfde eenheden als KNMI)."""
    code = hour.get("weather_code")
    return {
        "temperature_c": hour.get("temperature_2m"),
        "precipitation_mm": hour.get("precipitation"),
        "wind_speed_ms": hour.get("wind_speed_10m"),
        "wind_gust_ms": hour.get("wind_gusts_10m"),
        "visibility_m": hour.get("visibility"),
        "humidity_pct": hour.get("relative_humidity_2m"),
        "has_snow": int((hour.get("snowfall") or 0) > 0 or code in SNOW_CODES),
        "has_fog": int(code in FOG_CODES),
        "has_ice": int(code in ICE_CODES),
    }


class OpenMeteoClient:
    """Haalt per locatie en dag de uurverwachting op, met een kleine cache.

    Cache-sleutel: coördinaten op 2 decimalen (~1 km) + datum; geldig 30 minuten. Zo
    doet een pagina met 20 vertrekken van hetzelfde station maar één verzoek.
    """

    def __init__(self, ttl_seconds: int = 1800, timeout: float = 10.0):
        self.ttl = ttl_seconds
        self.timeout = timeout
        self._cache: dict[tuple, tuple[float, dict[int, dict]]] = {}

    def hourly(self, lat: float, lng: float, day: date) -> dict[int, dict]:
        """{uur (0-23): weerfeatures} voor één dag, in Nederlandse tijd."""
        key = (round(lat, 2), round(lng, 2), day)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        resp = requests.get(
            URL,
            params={
                "latitude": key[0],
                "longitude": key[1],
                "hourly": ",".join(HOURLY),
                "models": "knmi_seamless",
                "wind_speed_unit": "ms",
                "timezone": "Europe/Amsterdam",
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        hourly = resp.json()["hourly"]
        result = {}
        for i, ts in enumerate(hourly["time"]):
            hour = int(ts[11:13])
            result[hour] = to_features({k: hourly[k][i] for k in HOURLY})
        self._cache[key] = (time.monotonic(), result)
        return result

    def at(self, lat: float, lng: float, day: date, hour: int) -> dict:
        """Weerfeatures voor één uur; bij een fout lege waarden (het model kan NaN aan)."""
        try:
            return self.hourly(lat, lng, day).get(hour, dict.fromkeys(WEATHER_FEATURES))
        except (requests.RequestException, KeyError, ValueError) as err:
            log.warning("Weer niet beschikbaar (%s); voorspelling zonder weer", err)
            return dict.fromkeys(WEATHER_FEATURES)
