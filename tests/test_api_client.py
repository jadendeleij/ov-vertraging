"""Tests voor de client die het dashboard gebruikt (geen echte API nodig)."""

from datetime import datetime

import pytest
import requests

from ov_vertraging import api_client as api


class FakeResponse:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def test_predict_stuurt_juiste_json(monkeypatch):
    calls = {}

    def fake_request(method, url, timeout, **kwargs):
        calls.update(method=method, url=url, json=kwargs["json"])
        return FakeResponse(200, {"probability": 0.04})

    monkeypatch.setattr(api.requests, "request", fake_request)
    out = api.predict("http://x", "BGN", "RTB", datetime(2026, 11, 23, 17, 0))
    assert out == {"probability": 0.04}
    assert calls["method"] == "POST" and calls["url"] == "http://x/predict"
    assert calls["json"]["departure_time"] == "2026-11-23T17:00:00"


def test_foutmelding_uit_detail(monkeypatch):
    monkeypatch.setattr(
        api.requests,
        "request",
        lambda *a, **k: FakeResponse(404, {"detail": "Geen directe trein gevonden"}),
    )
    with pytest.raises(api.ApiError, match="Geen directe trein"):
        api.departures("http://x", "BGN")


def test_api_onbereikbaar(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("weg")

    monkeypatch.setattr(api.requests, "request", boom)
    with pytest.raises(api.ApiError, match="niet bereikbaar op http://x"):
        api.stations("http://x")


def test_format_pct():
    assert api.format_pct(0.0388) == "3,9 %"
    assert api.format_pct(None) == "–"
