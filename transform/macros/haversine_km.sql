{#
    Afstand in km tussen twee punten op aarde (haversine-formule, aardstraal 6371 km).
    Nauwkeurig genoeg voor "welk weerstation ligt het dichtst bij dit treinstation".
#}
{% macro haversine_km(lat1, lng1, lat2, lng2) -%}
    2 * 6371 * asin(sqrt(
        pow(sin(radians({{ lat2 }} - {{ lat1 }}) / 2), 2)
        + cos(radians({{ lat1 }})) * cos(radians({{ lat2 }}))
          * pow(sin(radians({{ lng2 }} - {{ lng1 }}) / 2), 2)
    ))
{%- endmacro %}
