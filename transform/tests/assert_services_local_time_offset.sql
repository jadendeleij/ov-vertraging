-- Nederlandse tijd ligt altijd 1 uur (winter) of 2 uur (zomer) voor op UTC.
-- Een ander verschil betekent een fout in de tijdzone-omrekening.

select stop_id, departure_time_local, departure_hour_utc
from {{ ref('stg_services') }}
where departure_time is not null
  and date_diff(
        'hour', departure_hour_utc, date_trunc('hour', departure_time_local)
      ) not in (1, 2)
