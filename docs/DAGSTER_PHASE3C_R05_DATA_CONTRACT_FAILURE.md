# Phase 3C R05 — dbt Data Contract Failure

## Purpose

R05 proves that a data-quality/contract violation is not treated like an infrastructure
incident. Re-running unchanged bad data is not a repair strategy.

```text
dbt data test executes
    -> run_results.json: test status = fail
    -> failure_class = data_contract
    -> Step Retry disabled
    -> Run FAILURE
    -> Recovery policy = ALERT_MANUAL
    -> Recovery Sensor emits no RunRequest
```

## Acceptance-only failure injection

`dbt/mercaso_dbt/tests/acceptance/r05_force_data_contract_failure.sql` is a singular
Data Test with tag `phase3c_r05_acceptance`.

Its control variable defaults to `false`, so normal project execution returns zero
violating rows. R05 explicitly sets:

```yaml
phase3c_r05_force_data_contract_failure: true
```

and the test returns one violating row. This keeps the production model/data path clean
while providing a deterministic Runtime Acceptance trigger.

## Evidence levels

### R05-A1 — Local Dagster + real dbt runtime

The acceptance harness executes the test through production
`execute_classified_dbt`. A job-level `max_retries=2` policy is deliberately configured;
`data_contract` must still execute exactly once because the adapter raises
`Failure(..., allow_retries=False)`.

Required evidence:

- `target/run_results.json` contains the R05 test with `status=fail`;
- Dagster Run status is `FAILURE`;
- Run tag `commerce/failure_class=data_contract`;
- source tag = `dbt_artifact`;
- component tag = `dbt:test`;
- reason tag = `dbt_data_test_failed`;
- asset attempts = 1 despite the surrounding retry policy.

### R05-A2 — Recovery fail-closed behavior

For the exact failed partition:

```text
freshness overdue = true
materialized      = false
failed run        = true
failure class     = data_contract
infrastructure    = healthy
```

must produce:

```text
ALERT_MANUAL / data_contract_failure
```

and the SensorDefinition must return `SkipReason`, not `RunRequest`.

### R05-B — Real daily-partition evidence

Later, in the real Docker + Dagster runtime, inject a controlled bad record or the
acceptance variable into the real partition execution and preserve the Run/Event/dbt
artifact evidence. Do not call R05 fully Runtime PASS from the local harness alone.

## Safety boundary

`test status=fail` is structured proof of a data contract violation. A dbt test node with
`status=error` is **not** automatically data-contract evidence; it remains `unknown`
because a SQL/runtime/adapter error may be the cause. Unknown failures fail closed.
