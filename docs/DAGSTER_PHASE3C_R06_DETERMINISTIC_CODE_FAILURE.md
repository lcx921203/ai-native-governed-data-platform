# Phase 3C R06 — Deterministic dbt Project / Code Failure

## Purpose

R06 proves that a deterministic dbt project/Jinja failure is not treated like a
transient runtime problem.

```text
dbt parse
  -> project/Jinja compiler error
  -> deterministic_code
  -> no Step Retry
  -> Daily Run FAILURE
  -> Recovery Policy ALERT_MANUAL
  -> Sensor SkipReason / no automatic replay
```

The acceptance probe uses `dbt parse`, not `dbt compile`, for an important evidence
reason: dbt parse does not connect to the warehouse, while dbt compile may require a
warehouse connection and introspective queries. Therefore a generic compile non-zero
exit is not sufficient by itself to prove deterministic code failure.

## Acceptance-only failure injection

`dbt/mercaso_dbt/models/acceptance/r06_deterministic_code_probe.sql` is valid by
default. Only R06 sets:

```text
phase3c_r06_force_parse_failure=true
```

which calls:

```jinja
{{ exceptions.raise_compiler_error('R06_FORCED_DETERMINISTIC_CODE_FAILURE') }}
```

Normal project parse/build behavior remains unchanged when the var is absent or false.

## Classification contract

```text
dbt parse non-zero
  -> DETERMINISTIC_CODE
  -> source = dbt_command
  -> reason = dbt_parse_failed

dbt compile non-zero without structured deterministic proof
  -> UNKNOWN
  -> fail closed
```

This intentionally removes the earlier over-broad assumption that every compile
failure is a code defect.

## Retry and recovery contract

Even if the Dagster job has `RetryPolicy(max_retries=2)`, the production dbt adapter
must raise the deterministic failure with retries disabled. R06-A1 therefore expects
one asset attempt only.

After a failed exact partition is persisted with `failure_class=deterministic_code`,
R06-A2 expects:

```text
RecoveryAction = ALERT_MANUAL
reason_code    = deterministic_code_failure
Sensor result  = SkipReason
auto replay    = none
auto budget    = 0
```

## Evidence boundary

R06-A is a local Dagster/dbt acceptance harness. It proves the classification, no-Step-
Retry rule, and manual recovery guard. It does not prove an operator actually fixed the
code, deployed it, or successfully replayed the corrected partition.
