-- Punctualiteit per traject x uur x weekdag, voor dashboard en analyse.
--
-- Let op voor fase 4: dit aggregeert over de HELE periode en bevat dus informatie
-- uit de toekomst t.o.v. een willekeurig vertrek. Niet als feature gebruiken; de
-- historische punctualiteit voor het model wordt alleen uit eerdere data berekend.

select
    route_code,
    station_code,
    next_station_code,
    departure_hour,
    departure_weekday,
    count(*) as n_departures,
    count(*) filter (is_cancelled) as n_cancelled,
    count(is_delayed_5min) as n_departed,
    count(*) filter (is_delayed_5min) as n_delayed_5min,
    round(avg(is_delayed_5min::int), 4) as pct_delayed_5min,
    round(avg(is_cancelled::int), 4) as pct_cancelled,
    round(avg(departure_delay_min) filter (not is_cancelled), 2) as avg_delay_min,
    quantile_cont(departure_delay_min, 0.9) filter (not is_cancelled) as p90_delay_min
from {{ ref('fct_departures') }}
group by all
