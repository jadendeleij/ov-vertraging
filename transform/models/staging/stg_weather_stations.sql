-- Eén rij per KNMI-weerstation, met coördinaten en welke variabelen het meet.
-- "Meet temperatuur" e.d. leiden we af uit de data: niet elk station meet alles
-- (zee- en windstations meten bv. geen temperatuur). Nodig om per variabele het
-- dichtstbijzijnde station te kiezen dat die variabele echt meet.

with measured as (
    select
        station_id,
        count(t) > 0 as measures_temperature,
        count(rh) > 0 as measures_precipitation,
        count(fh) > 0 as measures_wind,
        count(vv) > 0 as measures_visibility,
        count(*) as n_hours
    from {{ source('raw', 'weather_hourly') }}
    group by station_id
)

select
    s.station_id as weather_station_id,
    s.name as weather_station_name,
    s.lat,
    s.lon as lng,
    s.altitude_m,
    coalesce(m.measures_temperature, false) as measures_temperature,
    coalesce(m.measures_precipitation, false) as measures_precipitation,
    coalesce(m.measures_wind, false) as measures_wind,
    coalesce(m.measures_visibility, false) as measures_visibility,
    coalesce(m.n_hours, 0) as n_hours
from {{ source('raw', 'weather_stations') }} as s
left join measured as m using (station_id)
