-- Tijdzonetest rond de wisseling naar wintertijd (zondag 25 oktober 2026, 03:00 -> 02:00).
-- Voor De Bilt (260) moet lokaal 02:00 die nacht TWEE keer voorkomen (als 00:00 en 01:00
-- UTC), en moeten de uurvakken in UTC gewoon uniek blijven. Geeft rijen terug bij een fout.

with nacht as (
    select hour_start_utc, hour_start_local
    from {{ ref('stg_weather') }}
    where weather_station_id = 260
      and hour_start_utc >= timestamp '2026-10-25 00:00'
      and hour_start_utc < timestamp '2026-10-25 03:00'
)

select 'lokaal 02:00 komt niet twee keer voor' as fout, count(*) as n
from nacht
where hour_start_local = timestamp '2026-10-25 02:00'
having count(*) <> 2

union all

select 'verwachtte 3 UTC-uurvakken' as fout, count(*) as n
from nacht
having count(*) <> 3
