-- Eén rij per stop van een rit, opgeschoond.
-- * tijden: origineel met tijdzone + lokale Nederlandse tijd + UTC-uur (koppelsleutel weer)
-- * vertragingen buiten [-5, 600] min zijn datafouten (bv. DB-treinen met precies
--   ~1440 min = 24 uur) en worden NULL, met een vlag zodat je ze kunt terugvinden
-- * service_max_delay_min laten we bewust weg: staat alleen op de eerste stop ingevuld
--   en is bovendien leakage (bevat informatie uit de toekomst van de rit)

with source as (
    select * from {{ source('raw', 'services') }}
),

renamed as (
    select
        stop_id,
        service_id,
        service_date,
        service_type,
        company,
        train_number,
        service_type not ilike '%bus%'
            and service_type <> 'Metro ipv trein' as is_rail_service,
        service_completely_cancelled,
        service_partly_cancelled,

        station_code,
        station_name,

        arrival_time,
        timezone('{{ var("local_tz") }}', arrival_time) as arrival_time_local,
        case
            when arrival_delay_min between -5 and 600 then arrival_delay_min
        end as arrival_delay_min,
        arrival_cancelled,

        departure_time,
        timezone('{{ var("local_tz") }}', departure_time) as departure_time_local,
        case
            when departure_delay_min between -5 and 600 then departure_delay_min
        end as departure_delay_min,
        departure_cancelled,

        coalesce(arrival_delay_min not between -5 and 600, false)
            or coalesce(departure_delay_min not between -5 and 600, false)
            as has_invalid_delay,

        platform_change,
        planned_platform,
        actual_platform
    from source
)

select
    *,
    -- Afgeleide velden op basis van geplande vertrektijd (lokaal).
    hour(departure_time_local) as departure_hour,
    isodow(departure_time_local) as departure_weekday,  -- 1 = maandag, 7 = zondag
    -- Begin van het UTC-uur van vertrek. Lokaal 02:00 komt bij de wintertijdwissel
    -- twee keer voor; in UTC is elk uur uniek. Daarom koppelen we het weer hierop.
    date_trunc('hour', timezone('UTC', departure_time)) as departure_hour_utc
from renamed
