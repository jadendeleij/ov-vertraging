-- Eén rij per treinvertrek: de centrale feitentabel voor dashboard en model.
--
-- Target is_delayed_5min (keuze vastgelegd in README en docs/model.md):
--   true  = vertrokken met >= 5 min vertraging
--   false = vertrokken met < 5 min vertraging
--   NULL  = geannuleerd (is_cancelled) of vertraging onbekend/ongeldig
-- Het model voorspelt dus: "als de trein rijdt, wat is de kans op >= 5 min?".
-- Annuleringen blijven in de tabel voor het dashboard (apart cijfer, zoals NS dat doet).

select
    d.stop_id as departure_id,
    d.service_id,
    d.service_date,
    cast(d.departure_time_local as date) as departure_date,
    d.service_type,
    d.company,
    d.train_number,
    d.station_code,
    d.next_station_code,
    d.route_code,
    d.stop_sequence,
    d.n_stops,
    d.is_first_stop,

    d.departure_time,
    d.departure_time_local,
    d.departure_hour,
    d.departure_weekday,
    d.departure_hour_utc,

    -- uitkomst
    d.departure_cancelled as is_cancelled,
    d.departure_delay_min,
    case
        when d.departure_cancelled then null
        when d.departure_delay_min is null then null
        else d.departure_delay_min >= 5
    end as is_delayed_5min,

    -- Alleen voor analyse (bv. doorwerking van vertraging). VERBODEN als modelfeature:
    -- dit is informatie over dezelfde rit die op het voorspelmoment niet bekend is.
    d.arrival_delay_min,
    d.service_completely_cancelled,
    d.service_partly_cancelled,
    d.platform_change,

    -- weer van het uur van geplande vertrektijd (in productie: een verwachting)
    d.temperature_c,
    d.humidity_pct,
    d.precipitation_mm,
    d.wind_speed_ms,
    d.wind_gust_ms,
    d.wind_bft,
    d.wind_gust_bft,
    d.visibility_m,
    d.has_fog,
    d.has_snow,
    d.has_thunder,
    d.has_ice,
    d.temperature_c < 0 as is_frost,
    d.wind_bft >= 7 as is_wind_7bft,
    d.precipitation_mm >= 4 as is_heavy_rain
from {{ ref('int_departures_with_weather') }} as d
