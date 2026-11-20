-- Het target is_delayed_5min moet precies de afgesproken definitie volgen:
-- NULL bij annulering of onbekende vertraging, anders departure_delay_min >= 5.

select departure_id, is_cancelled, departure_delay_min, is_delayed_5min
from {{ ref('fct_departures') }}
where
    (is_cancelled and is_delayed_5min is not null)
    or (not is_cancelled and departure_delay_min is not null
        and is_delayed_5min is distinct from (departure_delay_min >= 5))
    or (departure_delay_min is null and is_delayed_5min is not null)
