"""Cijfers voor de pagina Inzichten, uit de voorgerekende aggregaten in de export.

Filters: periode (laatste N maanden) en treintype. Alles wordt gewogen opgeteld uit
tabellen per maand x treintype, zodat het in milliseconden klaar is.
"""

import duckdb

PERIODS = (3, 6, 12)
MIN_STATION_DEPARTURES = 300  # minder vertrekken: percentage te onbetrouwbaar voor de kaart


def available_months(con: duckdb.DuckDBPyConnection) -> list:
    return [
        r[0]
        for r in con.execute("SELECT DISTINCT maand FROM insights_station ORDER BY 1").fetchall()
    ]


def _where(months: list, service_type: str | None, alias: str = "") -> tuple[str, list]:
    p = f"{alias}." if alias else ""
    sql = f"{p}maand BETWEEN ? AND ?"
    params = [months[0], months[-1]]
    if service_type:
        sql += f" AND {p}service_type = ?"
        params.append(service_type)
    return sql, params


def _kpis(con, months: list, service_type: str | None) -> dict | None:
    if not months:
        return None
    w, params = _where(months, service_type)
    row = con.execute(
        f"""
        SELECT sum(n_all), sum(n_target), sum(n_delayed), sum(n_cancelled),
               sum(sum_delay), sum(n_delay)
        FROM insights_station WHERE {w}
        """,
        params,
    ).fetchone()
    n_all, n_target, n_delayed, n_cancelled, sum_delay, n_delay = row
    if not n_all:
        return None
    return {
        "departures": int(n_all),
        "punctuality": 1 - n_delayed / n_target if n_target else None,
        "avg_delay_min": sum_delay / n_delay if n_delay else None,
        "cancelled": n_cancelled / n_all,
    }


def compute(
    con: duckdb.DuckDBPyConnection, months: int = 12, service_type: str | None = None
) -> dict:
    """Alle cijfers voor Inzichten: KPI's (met vergelijking), kaart, heatmap en weer."""
    all_months = available_months(con)
    months = max(1, min(int(months), len(all_months)))
    period = all_months[-months:]
    previous = all_months[-2 * months : -months] if len(all_months) >= 2 * months else []

    kpis = _kpis(con, period, service_type)
    prev = _kpis(con, previous, service_type)
    if kpis and prev:
        kpis["delta"] = {
            "punctuality": kpis["punctuality"] - prev["punctuality"],
            "avg_delay_min": kpis["avg_delay_min"] - prev["avg_delay_min"],
            "cancelled": kpis["cancelled"] - prev["cancelled"],
        }

    w, params = _where(period, service_type, "i")
    stations = con.execute(
        f"""
        SELECT i.station_code AS code, any_value(s.station_name) AS name,
               any_value(s.lat) AS lat, any_value(s.lng) AS lng,
               sum(i.n_all) AS departures,
               1 - sum(i.n_delayed) / sum(i.n_target) AS punctuality
        FROM insights_station AS i JOIN stations AS s USING (station_code)
        WHERE {w} AND s.lat IS NOT NULL
        GROUP BY i.station_code HAVING sum(i.n_target) >= ?
        ORDER BY punctuality
        """,
        [*params, MIN_STATION_DEPARTURES],
    ).df()

    w, params = _where(period, service_type)
    heat = con.execute(
        f"""
        SELECT weekday, hour, sum(n_delayed) / sum(n_target) AS delayed
        FROM insights_hour WHERE {w} AND hour BETWEEN 5 AND 23
        GROUP BY ALL HAVING sum(n_target) > 0 ORDER BY ALL
        """,
        params,
    ).fetchall()
    weather = con.execute(
        f"""
        SELECT weer, sum(n_target) AS n, sum(n_delayed) / sum(n_target) AS delayed
        FROM insights_weather WHERE {w}
        GROUP BY 1 HAVING sum(n_target) >= 1000 ORDER BY delayed DESC
        """,
        params,
    ).fetchall()
    types = [
        r[0]
        for r in con.execute(
            "SELECT service_type FROM insights_station GROUP BY 1 ORDER BY sum(n_all) DESC, 1"
        ).fetchall()
    ]

    return {
        "period": {
            "months": months,
            "from": str(period[0]),
            "to": str(period[-1]),
            "compared_to": [str(previous[0]), str(previous[-1])] if previous else None,
        },
        "service_type": service_type,
        "service_types": types,
        "kpis": kpis,
        "stations": stations.to_dict(orient="records"),
        "heatmap": [{"weekday": d, "hour": h, "delayed": v} for d, h, v in heat],
        "weather": [{"condition": c, "departures": int(n), "delayed": v} for c, n, v in weather],
    }
