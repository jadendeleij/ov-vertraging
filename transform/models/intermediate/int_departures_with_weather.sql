-- Eén rij per treinvertrek (stop met een vertrektijd, alleen echte treinen),
-- met het volgende station (zodat een traject ontstaat) en het weer van dat uur.
--
-- Volgorde van stops: op geplande tijd, niet op stop_id. Bij omleidingen krijgt een
-- extra stop later een hoger stop_id (bv. via Dordrecht), terwijl die in tijd
-- tussen Rotterdam en Breda ligt.
--
-- Weer: per variabele van het dichtstbijzijnde station dat die variabele meet
-- (int_station_weather_map), gekoppeld op het UTC-uur van de geplande vertrektijd.

with stops as (
    select
        *,
        row_number() over w as stop_sequence,
        lead(station_code) over w as next_station_code,
        count(*) over (partition by service_id) as n_stops
    from {{ ref('stg_services') }}
    where is_rail_service
    window w as (
        partition by service_id
        order by coalesce(arrival_time, departure_time), stop_id
    )
),

departures as (
    select *
    from stops
    where departure_time is not null
      and next_station_code is not null
),

weather as (
    select * from {{ ref('stg_weather') }}
)

select
    d.stop_id,
    d.service_id,
    d.service_date,
    d.service_type,
    d.company,
    d.train_number,
    d.stop_sequence,
    d.n_stops,
    d.stop_sequence = 1 as is_first_stop,

    d.station_code,
    d.next_station_code,
    d.station_code || '-' || d.next_station_code as route_code,

    d.departure_time,
    d.departure_time_local,
    d.departure_hour,
    d.departure_weekday,
    d.departure_hour_utc,

    -- Uitkomst en (voor analyse) wat er op deze stop gebeurde. Let op: in het model
    -- (fase 4) zijn de vertraging/annulering van dezelfde rit verboden als feature.
    d.departure_delay_min,
    d.departure_cancelled,
    d.arrival_delay_min,
    d.service_completely_cancelled,
    d.service_partly_cancelled,
    d.has_invalid_delay,
    d.platform_change,

    m.temperature_station_id,
    m.precipitation_station_id,
    m.wind_station_id,
    m.visibility_station_id,

    wt.temperature_c,
    wt.humidity_pct,
    wp.precipitation_mm,
    ww.wind_speed_ms,
    ww.wind_gust_ms,
    ww.wind_bft,
    ww.wind_gust_bft,
    wv.visibility_m,
    wv.has_fog,
    wv.has_snow,
    wv.has_thunder,
    wv.has_ice

from departures as d
left join {{ ref('int_station_weather_map') }} as m
    on d.station_code = m.station_code
left join weather as wt
    on wt.weather_station_id = m.temperature_station_id
   and wt.hour_start_utc = d.departure_hour_utc
left join weather as wp
    on wp.weather_station_id = m.precipitation_station_id
   and wp.hour_start_utc = d.departure_hour_utc
left join weather as ww
    on ww.weather_station_id = m.wind_station_id
   and ww.hour_start_utc = d.departure_hour_utc
left join weather as wv
    on wv.weather_station_id = m.visibility_station_id
   and wv.hour_start_utc = d.departure_hour_utc
