-- Steekproef op dim_date met dagen die we zeker weten.

with expected as (
    select * from (values
        (date '2025-12-25', true,  'Eerste Kerstdag', 3),  -- kerstvakantie heel NL
        (date '2026-04-27', true,  'Koningsdag', 3),       -- valt in de meivakantie
        (date '2026-03-10', false, null, 0),               -- gewone dinsdag
        (date '2026-10-13', false, null, 1),               -- herfstvakantie alleen noord
        (date '2026-10-21', false, null, 2)                -- herfst midden + zuid
    ) as t(date_day, is_public_holiday, holiday_name, n_regions_school_holiday)
)

select e.*, d.is_public_holiday as got_holiday, d.n_regions_school_holiday as got_regions
from expected as e
left join {{ ref('dim_date') }} as d using (date_day)
where d.date_day is null
   or d.is_public_holiday <> e.is_public_holiday
   or d.holiday_name is distinct from e.holiday_name
   or d.n_regions_school_holiday <> e.n_regions_school_holiday
