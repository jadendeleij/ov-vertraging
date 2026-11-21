"""Live vertrektijden van de NS Reisinformatie API.

Key aanvragen: https://apiportal.ns.nl (product "Ns-App"), daarna in .env:
    NS_API_KEY=...
De key gaat mee in de header Ocp-Apim-Subscription-Key en komt nooit in git of logs.

Antwoorden worden 60 seconden gecachet per station, om de rate limit te respecteren.
"""

import logging
import time
from datetime import datetime

import requests

log = logging.getLogger(__name__)

URL = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v2/departures"

# NS-categoriecodes -> treintypes zoals het model ze kent.
CATEGORY_TO_SERVICE_TYPE = {
    "IC": "Intercity",
    "ICD": "Intercity direct",
    "SPR": "Sprinter",
    "SNG": "Sprinter",
    "ST": "Stoptrein",
    "SNE": "Sneltrein",
}


class NSNotConfigured(RuntimeError):
    """Er is geen NS_API_KEY ingesteld."""


def parse_departures(payload: dict, uic_to_code: dict[str, str]) -> list[dict]:
    """Zet het NS-antwoord om naar platte dicts met wat het model nodig heeft."""
    out = []
    for dep in payload.get("payload", {}).get("departures", []):
        product = dep.get("product", {}) or {}
        category = product.get("categoryCode") or dep.get("trainCategory")
        route = dep.get("routeStations") or []
        next_uic = str(route[0].get("uicCode")) if route else None
        planned = dep.get("plannedDateTime")
        out.append(
            {
                "train_number": _int(product.get("number")),
                "service_type": CATEGORY_TO_SERVICE_TYPE.get(
                    category, product.get("longCategoryName")
                ),
                "company": product.get("operatorName"),
                "direction": dep.get("direction"),
                "planned": datetime.strptime(planned, "%Y-%m-%dT%H:%M:%S%z") if planned else None,
                "planned_track": dep.get("plannedTrack"),
                "cancelled": bool(dep.get("cancelled", False)),
                "next_station_code": uic_to_code.get(next_uic) if next_uic else None,
            }
        )
    return out


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class NSClient:
    def __init__(self, api_key: str | None, ttl_seconds: int = 60, timeout: float = 10.0):
        self.api_key = api_key
        self.ttl = ttl_seconds
        self.timeout = timeout
        self._cache: dict[str, tuple[float, dict]] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def departures_raw(self, station_code: str) -> dict:
        if not self.configured:
            raise NSNotConfigured("NS_API_KEY ontbreekt; zie apiportal.ns.nl en .env.example")
        hit = self._cache.get(station_code)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        resp = requests.get(
            URL,
            params={"station": station_code, "maxJourneys": 25},
            headers={"Ocp-Apim-Subscription-Key": self.api_key},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._cache[station_code] = (time.monotonic(), data)
        return data
