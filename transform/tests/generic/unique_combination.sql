{#
    Eigen generieke test: de combinatie van kolommen is uniek (samengestelde sleutel).
    Zelf geschreven i.p.v. het dbt_utils-pakket, om geen extra dependency te hebben.
#}
{% test unique_combination(model, columns) %}
    select {{ columns | join(', ') }}, count(*) as n
    from {{ model }}
    group by {{ columns | join(', ') }}
    having count(*) > 1
{% endtest %}
