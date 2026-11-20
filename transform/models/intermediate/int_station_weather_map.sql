-- Elk treinstation -> per weervariabele het dichtstbijzijnde KNMI-station dat die
-- variabele ECHT meet. Niet elk weerstation meet alles (zeestations meten bv. geen
-- temperatuur), dus "het dichtstbijzijnde station" is per variabele een ander.
-- Verder weg dan weather_max_distance_km (bv. Berlijn, Brussel) = geen weer (NULL).

{% set variables = ['temperature', 'precipitation', 'wind', 'visibility'] %}

with pairs as (
    select
        s.station_code,
        w.weather_station_id,
        {{ haversine_km('s.lat', 's.lng', 'w.lat', 'w.lng') }} as distance_km,
        {% for v in variables %}w.measures_{{ v }}{{ "," if not loop.last }}
        {% endfor %}
    from {{ ref('stg_stations') }} as s
    cross join {{ ref('stg_weather_stations') }} as w
),

{% for v in variables %}
nearest_{{ v }} as (
    select station_code, weather_station_id, distance_km
    from pairs
    where measures_{{ v }}
    qualify row_number() over (
        partition by station_code order by distance_km, weather_station_id
    ) = 1
){{ "," if not loop.last }}
{% endfor %}

select
    s.station_code,
    s.station_name,
    s.is_dutch,
    {% for v in variables %}
    case when n_{{ v }}.distance_km <= {{ var('weather_max_distance_km') }}
        then n_{{ v }}.weather_station_id end as {{ v }}_station_id,
    round(n_{{ v }}.distance_km, 1) as {{ v }}_distance_km{{ "," if not loop.last }}
    {% endfor %}
from {{ ref('stg_stations') }} as s
{% for v in variables %}
left join nearest_{{ v }} as n_{{ v }} using (station_code)
{% endfor %}
