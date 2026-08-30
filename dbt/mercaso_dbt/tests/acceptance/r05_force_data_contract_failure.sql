{{ config(tags=['phase3c_r05_acceptance']) }}

-- Acceptance-only singular data test.
-- Normal project execution is unaffected because the switch defaults to false.
-- R05 explicitly turns the switch on to prove:
-- dbt test FAIL -> run_results.json -> Dagster data_contract classification.

{% if var('phase3c_r05_force_data_contract_failure', false) %}

select
    'R05_FORCED_DATA_CONTRACT_FAILURE' as violation_code

{% else %}

select
    'R05_FORCED_DATA_CONTRACT_FAILURE' as violation_code
where 1 = 0

{% endif %}
