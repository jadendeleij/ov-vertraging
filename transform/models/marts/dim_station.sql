-- Eén rij per station dat in de data voorkomt of in het stationsbestand staat.
-- Ongeveer 60 codes uit het treinarchief (vooral buitenland) staan niet in het
-- stationsbestand van 2023; die nemen we op met de naam uit het treinarchief en
-- zonder coördinaten, zodat elk vertrek in fct_departures een station heeft.

with unknown as (
    -- alle stops van treinritten, dus zowel vertrek- als eindstations
    select s.station_code, any_value(s.station_name) as station_name
    from {{ ref('stg_services') }} as s
    anti join {{ ref('stg_stations') }} as st using (station_code)
    where s.is_rail_service
    group by s.station_code
),

stations as (
    select
        station_code, station_name, country, is_dutch, station_type, lat, lng,
        true as in_station_file
    from {{ ref('stg_stations') }}
    union all
    select
        station_code, station_name, null, null, null, null, null,
        false as in_station_file
    from unknown
)

select
    s.*,
    m.temperature_station_id,
    m.temperature_distance_km,
    m.precipitation_station_id,
    m.wind_station_id,
    m.visibility_station_id
from stations as s
left join {{ ref('int_station_weather_map') }} as m using (station_code)
