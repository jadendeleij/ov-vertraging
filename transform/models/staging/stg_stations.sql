-- Eén rij per treinstation (incl. buitenland), met nette namen en coördinaten.

select
    station_code,
    uic_code,
    name_long as station_name,
    name_medium as station_name_medium,
    name_short as station_name_short,
    country,
    country = 'NL' as is_dutch,
    station_type,
    lat,
    lng
from {{ source('raw', 'stations') }}
