{#
    Eigen generieke test: elke niet-NULL waarde ligt tussen min_value en max_value.
    Geeft de foute rijen terug; 0 rijen = geslaagd.
    Gebruik in YAML:  data_tests: [{value_between: {min_value: -5, max_value: 600}}]
#}
{% test value_between(model, column_name, min_value, max_value) %}
    select {{ column_name }}
    from {{ model }}
    where {{ column_name }} is not null
      and {{ column_name }} not between {{ min_value }} and {{ max_value }}
{% endtest %}
