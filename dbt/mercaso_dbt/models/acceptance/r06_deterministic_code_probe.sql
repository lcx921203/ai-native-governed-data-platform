{{ config(
    materialized='view',
    tags=['phase3c_r06_acceptance']
) }}

{#
  Acceptance-only deterministic parser failure.

  Normal project behavior is valid because the var defaults to false. R06 explicitly
  enables the var so dbt raises a compiler error during `dbt parse`, which does not
  connect to the warehouse. This keeps the proof about project/Jinja code rather than
  infrastructure availability.
#}
{% if var('phase3c_r06_force_parse_failure', false) %}
  {{ exceptions.raise_compiler_error('R06_FORCED_DETERMINISTIC_CODE_FAILURE') }}
{% endif %}

select
    1 as acceptance_probe
