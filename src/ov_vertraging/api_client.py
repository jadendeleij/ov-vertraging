"""Kleine client voor de eigen FastAPI, gebruikt door het Streamlit-dashboard.

Los van Streamlit zodat het testbaar is. Fouten van de API komen terug als ApiError
met de (Nederlandse) melding uit het 'detail'-veld.
"""

from datetime import datetime

import requests

TIMEOUT = 30  # Render free: de API kan na slaap ~1 minuut nodig hebben om op te starten


class ApiError(RuntimeError):
    """De API gaf een fout of is niet bereikbaar."""


def _call(method: str, url: str, **kwargs) -> dict | list:
    try:
        resp = requests.request(method, url, timeout=kwargs.pop("timeout", TIMEOUT), **kwargs)
    except requests.RequestException as err:
        raise ApiError(f"De API is niet bereikbaar op {url.rsplit('/', 1)[0]}.") from err
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ApiError(str(detail))
    return resp.json()


def health(base_url: str) -> dict:
    return _call("GET", f"{base_url}/health", timeout=10)


def stations(base_url: str) -> list[dict]:
    return _call("GET", f"{base_url}/stations")


def predict(base_url: str, from_station: str, to_station: str, when: datetime) -> dict:
    return _call(
        "POST",
        f"{base_url}/predict",
        json={
            "from_station": from_station,
            "to_station": to_station,
            "departure_time": when.isoformat(),
        },
    )


def departures(base_url: str, station: str) -> dict:
    return _call("GET", f"{base_url}/departures/{station}")


def format_pct(p: float | None) -> str:
    """0.0388 -> '3,9 %' (Nederlandse komma)."""
    return "–" if p is None else f"{100 * p:.1f} %".replace(".", ",")
