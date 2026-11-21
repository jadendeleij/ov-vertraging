"""Tabbladen 'Voorspel mijn reis' en 'Live vertrekken': praten met de FastAPI.

Geen eigen modelcode hier: alles gaat via de API (ov_vertraging.api_client), zodat de
website en de API altijd hetzelfde antwoord geven.
"""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from ov_vertraging import api_client as api

RISK_COLORS = {"laag": "green", "gemiddeld": "orange", "hoog": "red"}


@st.cache_data(ttl=3600, show_spinner="Stations ophalen…")
def _stations(api_url: str) -> list[dict]:
    return api.stations(api_url)


def _station_picker(api_url: str, key: str, label: str, default: str) -> str | None:
    try:
        stations = _stations(api_url)
    except api.ApiError as err:
        st.error(f"{err} Start de API lokaal met `uvicorn api.main:app`.")
        return None
    names = [s["name"] for s in stations]
    index = names.index(default) if default in names else 0
    return st.selectbox(label, names, index=index, key=key)


def render_predict(api_url: str) -> None:
    st.write(
        "Kies je reis. We zoeken de eerste **directe trein** rond dat tijdstip en voorspellen "
        "de kans dat hij met **5 minuten of meer vertraging** vertrekt, met de "
        "weersverwachting van het KNMI erbij."
    )
    now = api.now_nl()  # niet datetime.now(): de server draait in UTC
    next_hour = api.next_full_hour(now)
    with st.form("voorspel"):
        a, b = st.columns(2)
        with a:
            origin = _station_picker(api_url, "van", "Van", "Utrecht Centraal")
        with b:
            destination = _station_picker(api_url, "naar", "Naar", "Amsterdam Centraal")
        c, d = st.columns(2)
        day = c.date_input(
            "Datum",
            value=next_hour.date(),
            min_value=now.date(),
            max_value=now.date() + timedelta(days=14),
            format="DD-MM-YYYY",
            help="Tot 14 dagen vooruit: verder reikt de weersverwachting niet.",
        )
        moment = d.time_input("Vertrektijd", value=next_hour.time(), step=timedelta(minutes=15))
        submitted = st.form_submit_button("Voorspel", type="primary")

    if not submitted or origin is None or destination is None:
        return
    if origin == destination:
        st.warning("Kies twee verschillende stations.")
        return
    try:
        with st.spinner("Voorspellen… (de eerste keer kan de online API even opstarten)"):
            r = api.predict(api_url, origin, destination, datetime.combine(day, moment))
    except api.ApiError as err:
        st.warning(str(err))
        return

    train = r["train"]
    dep = datetime.fromisoformat(train["planned_departure"])
    arr = train.get("planned_arrival")
    color = RISK_COLORS.get(r["risk_level"], "gray")

    st.subheader(f"{train['type']} {train['number']} · vertrekt {dep:%H:%M}")
    st.caption(
        f"{r['from_station']['name']} → {r['to_station']['name']}"
        + (f" · aankomst {datetime.fromisoformat(arr):%H:%M}" if arr else "")
        + (f" · eerstvolgende stop {train['next_stop']['name']}" if train.get("next_stop") else "")
        + f" · {train['operator']}"
    )
    m1, m2 = st.columns(2)
    m1.metric("Kans op ≥ 5 min vertraging", api.format_pct(r["probability"]))
    m2.markdown(
        f"**Risico:** :{color}[{r['risk_level'].upper()}]  \n"
        f"Gemiddelde kans volgens het model: {api.format_pct(r['average_probability'])}"
    )

    st.markdown("**Waarom?**")
    for f in r["factors"]:
        arrow = "🔺" if f["effect"] == "verhoogt" else "🔻"
        st.markdown(f"{arrow} {api.sentence(f['description'])} — *{f['effect']} de kans*")

    w = r["weather"]
    if w.get("temperature_c") is not None:
        bits = [
            f"{w['temperature_c']:.0f} °C",
            f"neerslag {w['precipitation_mm']:.1f} mm",
            f"windstoten {w['wind_gust_ms']:.0f} m/s",
        ]
        bits += [
            label
            for key, label in [("has_snow", "sneeuw"), ("has_fog", "mist"), ("has_ice", "ijzel")]
            if w.get(key)
        ]
        st.caption("Weersverwachting (KNMI via Open-Meteo): " + ", ".join(bits))
    st.caption(r["note"])


def render_live(api_url: str) -> None:
    st.write(
        "De eerstvolgende vertrekken van een station, live van de **NS**, met per trein de "
        "voorspelde kans op ≥ 5 minuten vertraging."
    )
    with st.form("live"):
        station = _station_picker(api_url, "live", "Station", "Utrecht Centraal")
        submitted = st.form_submit_button("Vertrekken ophalen", type="primary")
    if not submitted or station is None:
        return
    try:
        with st.spinner("Vertrekken ophalen…"):
            r = api.departures(api_url, station)
    except api.ApiError as err:
        st.warning(str(err))
        return

    rows = [
        {
            "Vertrek": datetime.fromisoformat(d["planned_departure"]).strftime("%H:%M")
            if d["planned_departure"]
            else "",
            "Trein": f"{d['type'] or ''} {d['train_number'] or ''}".strip(),
            "Richting": d["direction"],
            "Spoor": d["planned_track"],
            "Kans ≥ 5 min": "uitgevallen" if d["cancelled"] else api.format_pct(d["probability"]),
            "Risico": "" if d["cancelled"] else (d["risk_level"] or ""),
            "Nu (NS)": ("+" + str(d["current_delay_min"]) + " min")
            if d.get("current_delay_min")
            else "",
        }
        for d in r["departures"]
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.caption(
        "'Nu (NS)' is de actuele vertraging volgens NS, alleen ter vergelijking: het model "
        f"gebruikt die niet. Opgehaald om {api.now_nl():%H:%M}."
    )
