{% macro shopify_window_is_configured() %}
    {% set effective_start = var('shopify_effective_start', none) %}
    {% set effective_end = var('shopify_effective_end', none) %}

    {% if (effective_start is none) != (effective_end is none) %}
        {{ exceptions.raise_compiler_error(
            'shopify_effective_start and shopify_effective_end must be provided together'
        ) }}
    {% endif %}

    {{ return(effective_start is not none and effective_end is not none) }}
{% endmacro %}

{% macro shopify_window_predicate(column_name) %}
    {% set effective_start = var('shopify_effective_start', none) %}
    {% set effective_end = var('shopify_effective_end', none) %}

    {% if shopify_window_is_configured() %}
        {{ column_name }} >= to_timestamp('{{ effective_start }}')
        and {{ column_name }} < to_timestamp('{{ effective_end }}')
    {% else %}
        true
    {% endif %}
{% endmacro %}
