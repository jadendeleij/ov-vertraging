{#
    Omrekenmacro's voor KNMI-codes. Eén plek, zodat staging en latere modellen
    dezelfde definitie gebruiken.
#}

{# Windsnelheid in m/s -> Beaufort (0-12), KNMI-grenzen. #}
{% macro ms_to_beaufort(col) -%}
    case
        when {{ col }} is null then null
        when {{ col }} < 0.3  then 0
        when {{ col }} < 1.6  then 1
        when {{ col }} < 3.4  then 2
        when {{ col }} < 5.5  then 3
        when {{ col }} < 8.0  then 4
        when {{ col }} < 10.8 then 5
        when {{ col }} < 13.9 then 6
        when {{ col }} < 17.2 then 7
        when {{ col }} < 20.8 then 8
        when {{ col }} < 24.5 then 9
        when {{ col }} < 28.5 then 10
        when {{ col }} < 32.7 then 11
        else 12
    end
{%- endmacro %}

{#
    KNMI-zichtcode (VV) -> meters (ondergrens van de klasse).
    0 = < 100 m; 1-50 = code x 100 m; 56-80 = (code - 50) km;
    81-88 = 30 + (code - 80) x 5 km; 89 = > 70 km. 51-55 bestaan niet.
#}
{% macro knmi_visibility_m(col) -%}
    case
        when {{ col }} is null then null
        when {{ col }} = 0 then 0
        when {{ col }} between 1 and 50 then {{ col }} * 100
        when {{ col }} between 56 and 80 then ({{ col }} - 50) * 1000
        when {{ col }} between 81 and 88 then (30 + ({{ col }} - 80) * 5) * 1000
        when {{ col }} = 89 then 70000
    end
{%- endmacro %}

{# KNMI-hoeveelheid in tienden waarbij -1 "minder dan 0,05" betekent -> 0. #}
{% macro knmi_tenths_with_trace(col) -%}
    case when {{ col }} = -1 then 0.0 else {{ col }} / 10.0 end
{%- endmacro %}
