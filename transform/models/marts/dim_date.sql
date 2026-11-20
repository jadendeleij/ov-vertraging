-- Eén rij per kalenderdag in de periode van de data, met feestdagen,
-- schoolvakanties per regio en seizoen.
--
-- Schoolvakanties verschillen per regio. Stations hebben geen regio in de bron, dus
-- we geven per regio een vlag plus het aantal regio's met vakantie (0-3); dat laatste
-- is een bruikbare maat voor "hoe rustig is het in de spits".

with bounds as (
    select
        min(service_date) as first_day,
        -- nachttreinen na middernacht vallen op de dag erna
        max(service_date) + 1 as last_day
    from {{ ref('stg_services') }}
),

spine as (
    select cast(d as date) as date_day
    from bounds, generate_series(first_day, last_day, interval 1 day) as t(d)
),

school as (
    select
        s.date_day,
        bool_or(h.region = 'noord') as is_school_holiday_noord,
        bool_or(h.region = 'midden') as is_school_holiday_midden,
        bool_or(h.region = 'zuid') as is_school_holiday_zuid,
        any_value(h.vacation_name) as school_holiday_name
    from spine as s
    join {{ ref('nl_school_holidays') }} as h
        on s.date_day between h.start_date and h.end_date
    group by s.date_day
)

select
    s.date_day,
    year(s.date_day) as year,
    month(s.date_day) as month,
    day(s.date_day) as day_of_month,
    isodow(s.date_day) as weekday,  -- 1 = maandag
    week(s.date_day) as iso_week,
    isodow(s.date_day) >= 6 as is_weekend,
    case
        when month(s.date_day) in (12, 1, 2) then 'winter'
        when month(s.date_day) in (3, 4, 5) then 'lente'
        when month(s.date_day) in (6, 7, 8) then 'zomer'
        else 'herfst'
    end as season,

    p.holiday_date is not null as is_public_holiday,
    p.holiday_name,

    coalesce(sh.is_school_holiday_noord, false) as is_school_holiday_noord,
    coalesce(sh.is_school_holiday_midden, false) as is_school_holiday_midden,
    coalesce(sh.is_school_holiday_zuid, false) as is_school_holiday_zuid,
    coalesce(sh.is_school_holiday_noord::int, 0)
        + coalesce(sh.is_school_holiday_midden::int, 0)
        + coalesce(sh.is_school_holiday_zuid::int, 0) as n_regions_school_holiday,
    sh.school_holiday_name,

    -- "Gewone werkdag": geen weekend, feestdag of vakantie in welke regio dan ook.
    isodow(s.date_day) < 6
        and p.holiday_date is null
        and sh.date_day is null as is_regular_workday
from spine as s
left join {{ ref('nl_public_holidays') }} as p on s.date_day = p.holiday_date
left join school as sh on s.date_day = sh.date_day
