"""FastAPI: kans op ≥ 5 minuten vertraging, voor een reis of live per station.

Starten (vanuit de projectroot, na `python -m ov_vertraging.serving.export`):
    uvicorn api.main:app --reload
Documentatie (interactief): http://localhost:8000/docs

Endpoints:
    GET  /health                  status, modelversie, datum van de data
    POST /predict                 {van, naar, vertrektijd} -> trein + kans + uitleg
    GET  /departures/{station}    live NS-vertrekken met per trein de kans (NS_API_KEY nodig)

Omgevingsvariabelen: SERVING_DIR (standaard models/serving), NS_API_KEY.
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ov_vertraging.config import PROJECT_ROOT
from ov_vertraging.serving.ns import NSClient, NSNotConfigured, parse_departures
from ov_vertraging.serving.predictor import Departure, Predictor
from ov_vertraging.serving.weather import OpenMeteoClient

log = logging.getLogger(__name__)
ATTRIBUTION = (
    "Treindata: Rijden de Treinen (CC BY 4.0). Weer: KNMI via Open-Meteo. Live vertrektijden: NS."
)


# ---------- schema's ----------


class PredictRequest(BaseModel):
    from_station: str = Field(examples=["Bergen op Zoom"], description="Stationsnaam of -code")
    to_station: str = Field(examples=["Rotterdam Blaak"], description="Stationsnaam of -code")
    departure_time: datetime = Field(
        examples=["2026-11-23T17:00:00"],
        description="Gewenste vertrektijd; zonder tijdzone = Nederlandse tijd",
    )


class Station(BaseModel):
    code: str
    name: str | None


class Factor(BaseModel):
    feature: str
    description: str
    effect: str = Field(description="'verhoogt' of 'verlaagt' de kans")
    impact: float = Field(description="Bijdrage in log-odds (SHAP), t.o.v. een gemiddeld vertrek")


class Train(BaseModel):
    number: int | None
    type: str | None
    operator: str | None
    planned_departure: datetime
    planned_arrival: datetime | None
    next_stop: Station | None


class PredictResponse(BaseModel):
    from_station: Station
    to_station: Station
    train: Train
    probability: float = Field(description="Kans op ≥ 5 min vertraging bij vertrek (0-1)")
    risk_level: str = Field(description="laag / gemiddeld / hoog t.o.v. het landelijk gemiddelde")
    average_probability: float = Field(
        description="Gemiddelde kans volgens het model; de factoren zijn hiertegen afgezet"
    )
    factors: list[Factor]
    weather: dict
    model_version: str
    note: str
    attribution: str = ATTRIBUTION


class LiveDeparture(BaseModel):
    train_number: int | None
    type: str | None
    operator: str | None
    direction: str | None
    planned_departure: datetime | None
    planned_track: str | None
    actual_departure: datetime | None = Field(
        None,
        description="Actuele verwachte vertrektijd volgens NS (live, niet gebruikt door het model)",
    )
    current_delay_min: int | None = Field(None, description="Actuele vertraging volgens NS")
    cancelled: bool
    probability: float | None
    risk_level: str | None


class DeparturesResponse(BaseModel):
    station: Station
    departures: list[LiveDeparture]
    model_version: str
    attribution: str = ATTRIBUTION


def _delay_min(planned: datetime | None, actual: datetime | None) -> int | None:
    if planned is None or actual is None:
        return None
    return round((actual - planned).total_seconds() / 60)


# ---------- app ----------


def create_app(predictor: Predictor | None = None, ns: NSClient | None = None) -> FastAPI:
    """App-fabriek: tests geven een eigen predictor en NS-client mee."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        load_dotenv(PROJECT_ROOT / ".env")
        serving_dir = Path(os.getenv("SERVING_DIR", PROJECT_ROOT / "models" / "serving"))
        app.state.predictor = predictor or Predictor(serving_dir, OpenMeteoClient())
        app.state.ns = ns or NSClient(os.getenv("NS_API_KEY"))
        yield

    app = FastAPI(
        title="OV-vertragingsvoorspeller",
        description="Kans dat een Nederlandse trein met ≥ 5 minuten vertraging vertrekt. "
        + ATTRIBUTION,
        version="1.0",
        lifespan=lifespan,
    )

    def get_predictor(request: Request) -> Predictor:
        return request.app.state.predictor

    def get_ns(request: Request) -> NSClient:
        return request.app.state.ns

    PredictorDep = Annotated[Predictor, Depends(get_predictor)]
    NSDep = Annotated[NSClient, Depends(get_ns)]

    def station_or_404(p: Predictor, text: str) -> str:
        code = p.resolve_station(text)
        if code is None:
            hint = p.suggest_stations(text)
            msg = f"Station '{text}' niet gevonden."
            raise HTTPException(404, msg + (f" Bedoel je: {', '.join(hint)}?" if hint else ""))
        return code

    @app.get("/stations", response_model=list[Station])
    def stations(p: PredictorDep) -> list[Station]:
        """Alle Nederlandse stations met vertrekken (voor keuzelijsten)."""
        return [Station(**s) for s in p.station_list()]

    @app.get("/health")
    def health(p: PredictorDep, ns: NSDep) -> dict:
        return {
            "status": "ok",
            "model_version": p.meta["version"],
            "history_until": p.manifest["history_until"],
            "timetable_days": p.manifest["timetable_days"],
            "ns_configured": ns.configured,
        }

    @app.post("/predict", response_model=PredictResponse)
    def predict(req: PredictRequest, p: PredictorDep) -> PredictResponse:
        origin = station_or_404(p, req.from_station)
        destination = station_or_404(p, req.to_station)
        if origin == destination:
            raise HTTPException(422, "Vertrek- en aankomststation zijn hetzelfde.")
        trip = p.find_trip(origin, destination, req.departure_time)
        if trip is None:
            raise HTTPException(
                404,
                f"Geen directe trein gevonden van {p.station_name(origin)} naar "
                f"{p.station_name(destination)} rond dit tijdstip. Reizen met een overstap "
                "worden (nog) niet ondersteund.",
            )
        dep = Departure(
            station_code=origin,
            next_station_code=trip["next_station_code"],
            service_type=trip["service_type"],
            company=trip["company"],
            train_number=trip["train_number"],
            stop_sequence=trip["stop_sequence"],
            planned=trip["planned_departure"],
        )
        result = p.predict([dep])[0]
        nxt = trip["next_station_code"]
        return PredictResponse(
            from_station=Station(code=origin, name=p.station_name(origin)),
            to_station=Station(code=destination, name=p.station_name(destination)),
            train=Train(
                number=trip["train_number"],
                type=trip["service_type"],
                operator=trip["company"],
                planned_departure=trip["planned_departure"],
                planned_arrival=trip.get("planned_arrival"),
                next_stop=Station(code=nxt, name=p.station_name(nxt)) if nxt else None,
            ),
            probability=result["probability"],
            risk_level=result["risk_level"],
            average_probability=result["model_average"],
            factors=[Factor(**f) for f in result["factors"]],
            weather=result["weather"],
            model_version=p.meta["version"],
            note=(
                "Trein gezocht in de dienstregeling van dezelfde weekdag (laatste week van "
                "de data). Kans geldt voor vertrek uit het eerste station. Storingen en "
                "incidenten kan het model niet zien aankomen."
            ),
        )

    @app.get("/departures/{station}", response_model=DeparturesResponse)
    def departures(station: str, p: PredictorDep, ns: NSDep) -> DeparturesResponse:
        code = station_or_404(p, station)
        try:
            raw = ns.departures_raw(code)
        except NSNotConfigured as err:
            raise HTTPException(503, str(err)) from err
        except requests.RequestException as err:
            log.warning("NS API-fout: %s", err)
            raise HTTPException(502, "De NS API is nu niet bereikbaar.") from err

        uic_to_code = {str(int(u)): c for c, u in p.stations.uic_code.dropna().items()}
        live = parse_departures(raw, uic_to_code)
        to_predict = [
            (
                i,
                Departure(
                    station_code=code,
                    next_station_code=d["next_station_code"],
                    service_type=d["service_type"],
                    company=d["company"],
                    train_number=d["train_number"],
                    stop_sequence=p.stop_sequence_for(
                        d["train_number"], code, d["planned"].isoweekday()
                    ),
                    planned=d["planned"],
                ),
            )
            for i, d in enumerate(live)
            if d["planned"] is not None and not d["cancelled"]
        ]
        preds = dict(
            zip(
                [i for i, _ in to_predict],
                p.predict([dep for _, dep in to_predict]),
                strict=True,
            )
        )
        return DeparturesResponse(
            station=Station(code=code, name=p.station_name(code)),
            departures=[
                LiveDeparture(
                    train_number=d["train_number"],
                    type=d["service_type"],
                    operator=d["company"],
                    direction=d["direction"],
                    planned_departure=d["planned"],
                    planned_track=d["planned_track"],
                    actual_departure=d["actual"],
                    current_delay_min=_delay_min(d["planned"], d["actual"]),
                    cancelled=d["cancelled"],
                    probability=preds[i]["probability"] if i in preds else None,
                    risk_level=preds[i]["risk_level"] if i in preds else None,
                )
                for i, d in enumerate(live)
            ],
            model_version=p.meta["version"],
        )

    return app


app = create_app()
