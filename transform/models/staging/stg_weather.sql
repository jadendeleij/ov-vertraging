-- Eén rij per weerstation per uur, omgerekend naar gewone eenheden.
-- KNMI: uur 1-24 in UTC, "uur 5" = 04:00-05:00 UTC. We slaan het BEGIN van het uur op
-- (hour_start_utc), zodat het past op departure_hour_utc uit stg_services.

with source as (
    select * from {{ source('raw', 'weather_hourly') }}
),

converted as (
    select
        station_id as weather_station_id,
        date + interval (hour - 1) hour as hour_start_utc,

        t / 10.0 as temperature_c,
        {{ knmi_tenths_with_trace('rh') }} as precipitation_mm,
        fh / 10.0 as wind_speed_ms,
        fx / 10.0 as wind_gust_ms,
        case when dd in (0, 990) then null else dd end as wind_direction_deg,
        {{ knmi_tenths_with_trace('sq') }} as sunshine_h,
        {{ knmi_visibility_m('vv') }} as visibility_m,
        case when n = 9 then null else n end as cloud_cover_octa,
        u as humidity_pct,
        m::boolean as has_fog,
        s::boolean as has_snow,
        o::boolean as has_thunder,
        y::boolean as has_ice
    from source
)

select
    *,
    timezone('{{ var("local_tz") }}', timezone('UTC', hour_start_utc)) as hour_start_local,
    {{ ms_to_beaufort('wind_speed_ms') }} as wind_bft,
    {{ ms_to_beaufort('wind_gust_ms') }} as wind_gust_bft
from converted
