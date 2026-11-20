"""Streamlit-dashboard: punctualiteit van het Nederlandse spoor.

Starten (vanuit de projectroot):
    streamlit run app/dashboard.py

Leest alleen uit de dbt-marts in data/ov.duckdb (read-only). Alle SQL staat in
ov_vertraging.dashboard_queries; dit bestand regelt alleen de opmaak.
"""

import duckdb
import plotly.express as px
import streamlit as st

from ov_vertraging import dashboard_queries as dq
from ov_vertraging.config import DB_PATH

st.set_page_config(page_title="OV-vertraging", page_icon="🚆", layout="wide")

WEEKDAGEN = ["ma", "di", "wo", "do", "vr", "za", "zo"]
RED_SCALE = "YlOrRd"


# ---------- data ----------


@st.cache_resource
def get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=True)


def con() -> duckdb.DuckDBPyConnection:
    # Eigen cursor per aanroep: Streamlit draait sessies in verschillende threads.
    return get_connection().cursor()


@st.cache_data(ttl=3600, show_spinner=False)
def cached(name: str, *args):
    """Cache per query + filters, zodat terugklikken direct gaat."""
    return getattr(dq, name)(con(), *args)


if not DB_PATH.exists():
    st.error(
        "Geen database gevonden. Draai eerst de pipeline: "
        "`python -m ov_vertraging.load` en `cd transform && dbt build` (of via Dagster)."
    )
    st.stop()


# ---------- filters ----------

first_day, last_day = cached("date_bounds")
all_types = cached("service_types")

with st.sidebar:
    st.header("Filters")
    period = st.date_input(
        "Periode",
        value=(first_day, last_day),
        min_value=first_day,
        max_value=last_day,
        format="DD-MM-YYYY",
    )
    types = st.multiselect(
        "Treintype",
        options=all_types,
        default=[t for t in all_types if t in dq.DOMESTIC_TYPES],
        help="Standaard alleen binnenlandse treinen; internationale treinen nemen vaak "
        "al vertraging mee van over de grens.",
    )
    st.caption(
        "**Vertraagd** = vertrokken met ≥ 5 min vertraging. Geannuleerde vertrekken "
        "tellen apart (uitval)."
    )

if not isinstance(period, tuple) or len(period) != 2:
    st.info("Kies een begin- én einddatum.")
    st.stop()

f = dq.Filters(start=period[0], end=period[1], service_types=tuple(types))


# ---------- kop + KPI's ----------

st.title("🚆 Hoe op tijd rijdt de trein?")
st.caption(
    f"Treinvertrekken in Nederland van {f.start:%d-%m-%Y} t/m {f.end:%d-%m-%Y}, "
    "gekoppeld aan het KNMI-weer van dat uur."
)


def nl(x: float, decimals: int = 1) -> str:
    """Getal met Nederlandse decimale komma."""
    return f"{x or 0:.{decimals}f}".replace(".", ",")


def aantal(n: float) -> str:
    """Kort genoeg voor een KPI-tegel, ook in een smal venster."""
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{nl(n / 1_000_000)} mln"
    return f"{n:,}".replace(",", ".")


k = cached("kpis", f)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Vertrekken", aantal(k["vertrekken"]))
c2.metric("≥ 5 min vertraagd", f"{nl(k['pct_vertraagd'])} %")
c3.metric("Uitgevallen", f"{nl(k['pct_uitval'])} %")
c4.metric("Gem. vertraging", f"{nl(k['gem_vertraging'])} min")

tab_overzicht, tab_kaart, tab_weer, tab_traject = st.tabs(
    ["📊 Overzicht", "🗺️ Kaart", "🌧️ Weer", "🔍 Mijn traject"]
)

# ---------- overzicht ----------

with tab_overzicht:
    hw = cached("hour_weekday", f)
    hw = hw[hw.uur.between(5, 23)]
    if hw.empty:
        st.info("Geen data voor deze filters.")
    else:
        heat = hw.pivot(index="weekdag", columns="uur", values="pct").sort_index()
        heat.index = [WEEKDAGEN[i - 1] for i in heat.index]
        fig = px.imshow(
            heat,
            color_continuous_scale=RED_SCALE,
            aspect="auto",
            labels={"x": "vertrekuur", "y": "", "color": "% vertraagd"},
            title="% vertraagd per weekdag en vertrekuur",
        )
        st.plotly_chart(fig, use_container_width=True)

        days = cached("per_day", f)
        days_long = days.melt(
            id_vars="datum",
            value_vars=["pct_vertraagd", "pct_uitval"],
            var_name="maat",
            value_name="pct",
        ).replace({"pct_vertraagd": "% vertraagd", "pct_uitval": "% uitgevallen"})
        fig = px.line(
            days_long,
            x="datum",
            y="pct",
            color="maat",
            color_discrete_map={"% vertraagd": "#d62728", "% uitgevallen": "#4c78a8"},
            labels={"pct": "%", "datum": "", "maat": ""},
            title="Per dag: vertraging en uitval",
        )
        st.plotly_chart(fig, use_container_width=True)

# ---------- kaart ----------

with tab_kaart:
    dutch_only = st.toggle(
        "Alleen Nederlandse stations",
        value=True,
        help="Grensregionale treinen vertrekken ook vanaf Duitse en Belgische stations.",
    )
    st_df = cached("stations", f, 500, dutch_only)
    if st_df.empty:
        st.info("Geen data voor deze filters.")
    else:
        cap = st_df.pct_vertraagd.quantile(0.95)  # uitschieters niet de schaal laten bepalen
        fig = px.scatter_map(
            st_df,
            lat="lat",
            lon="lng",
            color="pct_vertraagd",
            size="vertrekken",
            hover_name="station_name",
            hover_data={
                "lat": False,
                "lng": False,
                "vertrekken": ":,",
                "pct_vertraagd": ":.1f",
                "pct_uitval": ":.1f",
            },
            color_continuous_scale=RED_SCALE,
            range_color=(0, cap),
            size_max=18,
            zoom=6.3,
            center={"lat": 52.15, "lon": 5.4},
            map_style="carto-positron",
            height=650,
            labels={"pct_vertraagd": "% vertraagd", "pct_uitval": "% uitval"},
        )
        fig.update_layout(margin={"l": 0, "r": 0, "t": 0, "b": 0})
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Elke stip is een station (≥ 500 vertrekken in de gekozen periode). Grootte = "
            "aantal vertrekken, kleur = % vertrekken ≥ 5 min vertraagd."
        )
        st.dataframe(
            st_df.sort_values("pct_vertraagd", ascending=False).head(15)[
                ["station_name", "vertrekken", "pct_vertraagd", "pct_uitval"]
            ],
            hide_index=True,
            use_container_width=True,
            column_config={
                "station_name": "Station",
                "vertrekken": st.column_config.NumberColumn("Vertrekken", format="%d"),
                "pct_vertraagd": st.column_config.NumberColumn("% vertraagd", format="%.1f"),
                "pct_uitval": st.column_config.NumberColumn("% uitval", format="%.1f"),
            },
        )

# ---------- weer ----------

with tab_weer:
    left, right = st.columns([3, 2])
    we = cached("weather_effects", f)
    we["kleur"] = we.weer.eq("Normaal (alles)").map({True: "normaal", False: "weer"})
    fig = px.bar(
        we,
        x="pct",
        y="weer",
        orientation="h",
        color="kleur",
        color_discrete_map={"normaal": "#7f7f7f", "weer": "#d62728"},
        hover_data={"vertrekken": ":,", "kleur": False},
        labels={"pct": "% vertraagd", "weer": ""},
        title="% vertraagd per weertype",
    )
    fig.update_layout(showlegend=False)
    left.plotly_chart(fig, use_container_width=True)

    tc = cached("temperature_curve", f)
    fig = px.line(
        tc,
        x="temperatuur",
        y="pct",
        markers=True,
        labels={"temperatuur": "temperatuur (°C)", "pct": "% vertraagd"},
        title="Kou én hitte zijn slecht",
    )
    right.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Weer van het dichtstbijzijnde KNMI-station dat de variabele meet, in het uur van "
        "vertrek. Dit zijn verbanden, geen oorzaken: vorst valt bv. vooral in de winter."
    )

# ---------- traject ----------

with tab_traject:
    st.write(
        "Kies een vertrek- en aankomststation. We zoeken alle **directe treinen** die eerst "
        "in A stoppen en daarna in B, en kijken hoe vaak ze ≥ 5 min te laat in B aankwamen."
    )
    opts = cached("station_options")
    names = dict(zip(opts.station_name, opts.station_code, strict=True))
    station_list = list(names)

    def idx(name: str) -> int:
        return station_list.index(name) if name in station_list else 0

    a, b = st.columns(2)
    origin = a.selectbox("Van", station_list, index=idx("Bergen op Zoom"))
    destination = b.selectbox("Naar", station_list, index=idx("Rotterdam Blaak"))

    if origin == destination:
        st.info("Kies twee verschillende stations.")
    else:
        with st.spinner("Ritten zoeken…"):
            summary = cached("trip_summary", names[origin], names[destination], f)
        if summary.empty:
            st.warning(
                f"Geen directe treinen gevonden van {origin} naar {destination} met deze "
                "filters. (Reizen met een overstap tellen hier niet mee.)"
            )
        else:
            total = summary.ritten.sum()
            late = (summary.pct_aankomst_5min_laat * summary.ritten).sum() / total
            cancelled = (summary.pct_uitgevallen * summary.ritten).sum() / total
            m1, m2, m3 = st.columns(3)
            m1.metric("Directe ritten", f"{total:,}".replace(",", "."))
            m2.metric("Aankomst ≥ 5 min te laat", f"{late:.1f} %")
            m3.metric("Uitgevallen", f"{cancelled:.1f} %")

            by_hour = cached("trip_by_hour", names[origin], names[destination], f)
            fig = px.bar(
                by_hour[by_hour.ritten >= 20],
                x="uur",
                y="pct_aankomst_5min_laat",
                hover_data={"ritten": True, "pct_uitgevallen": ":.1f"},
                labels={"uur": "vertrekuur", "pct_aankomst_5min_laat": "% ≥ 5 min te laat"},
                title=f"{origin} → {destination}: % te laat aangekomen per vertrekuur",
                color_discrete_sequence=["#d62728"],
            )
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(
                summary,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "treintype": "Treintype",
                    "ritten": st.column_config.NumberColumn("Ritten", format="%d"),
                    "reistijd_min": st.column_config.NumberColumn("Reistijd (min)", format="%d"),
                    "pct_uitgevallen": st.column_config.NumberColumn("% uitval", format="%.1f"),
                    "pct_aankomst_5min_laat": st.column_config.NumberColumn(
                        "% ≥ 5 min te laat", format="%.1f"
                    ),
                    "gem_vertraging_aankomst": st.column_config.NumberColumn(
                        "Gem. vertraging (min)", format="%.1f"
                    ),
                },
            )

# ---------- bron ----------

st.divider()
st.caption(
    "Bron treindata en stations: [Rijden de Treinen](https://www.rijdendetreinen.nl/open-data) "
    "(licentie [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)). "
    "Weer: [KNMI](https://www.knmi.nl/nederland-nu/klimatologie/uurgegevens). "
    "Feestdagen en schoolvakanties: Rijksoverheid. "
    "Code: [github.com/jadendeleij/ov-vertraging](https://github.com/jadendeleij/ov-vertraging)."
)
