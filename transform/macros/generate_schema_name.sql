{#
    Standaard plakt dbt het schema achter het target-schema ("main_staging").
    Wij willen gewoon "staging", "intermediate" en "marts" naast "raw".
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
