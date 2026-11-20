-- Minstens 95 % van de vertrekken vanaf Nederlandse stations heeft een temperatuur,
-- neerslag en windsnelheid. Lager betekent dat de koppeling (UTC-uur of
-- stationsmapping) stuk is. Geeft een rij terug bij een fout.

with nl as (
    select d.*
    from {{ ref('int_departures_with_weather') }} as d
    join {{ ref('stg_stations') }} as s using (station_code)
    where s.is_dutch
),

coverage as (
    select
        avg((temperature_c is not null)::int) as temperature,
        avg((precipitation_mm is not null)::int) as precipitation,
        avg((wind_speed_ms is not null)::int) as wind
    from nl
)

select *
from coverage
where temperature < 0.95 or precipitation < 0.95 or wind < 0.95
