# Phase 3C R12 — Unknown Failure → Fail Closed

R12 is the terminal safety guard for the structured failure-classification model.
Automation may act only on failure meaning that the owning layer can prove.

```text
ambiguous non-zero / missing class / invalid class
    -> unknown
    -> no Step Retry
    -> no cross-run Auto Replay
    -> ALERT_MANUAL / unknown_failure_class
```

## Why UNKNOWN is different

`UNKNOWN` does not mean the failure is permanently unrecoverable. It means the system
currently lacks enough structured evidence to prove that retrying is safe or useful.
Free-text stdout/stderr is therefore diagnostic context only; it cannot upgrade the
failure into `transient_runtime`, `infrastructure_unavailable`, `deterministic_code`, or
`data_contract`.

## Step Retry contract

The retry whitelist is intentionally positive:

```text
transient_runtime           -> Step Retry allowed (bounded)
infrastructure_unavailable  -> Step Retry allowed (bounded)
deterministic_code          -> no Step Retry
data_contract               -> no Step Retry
unknown                     -> no Step Retry
```

A job may still define `RetryPolicy(max_retries=2)`. The execution adapter raises a
structured Dagster `Failure` whose `allow_retries` value is derived from this whitelist,
so an UNKNOWN failure bypasses that job-level retry policy.

## Cross-run recovery contract

After the Freshness deadline, a failed exact partition with `failure_class=unknown` is:

```text
failed_run=true
materialized=false
active_run=false
infrastructure_healthy=true
failure_class=unknown
    -> ALERT_MANUAL
    -> unknown_failure_class
    -> Sensor SkipReason
    -> no Recovery RunRequest
```

Missing or invalid `commerce/failure_class` Run tags are also read as `UNKNOWN`; absent
classification evidence must not silently become replay permission.

## R12-A local acceptance harness

`acceptance/phase3c/r12_unknown_failure_fail_closed.py` proves two local contracts:

1. Production `SparkComposeResource` sees Docker command available + service healthy +
   unexplained non-zero return code, classifies `UNKNOWN`, and performs one asset attempt
   even though the probe job has `RetryPolicy(max_retries=2)`.
2. A persisted failed Daily Run tagged `UNKNOWN` is read by production recovery state,
   resolved to `ALERT_MANUAL / unknown_failure_class`, and the production SensorDefinition
   emits no automatic RunRequest.

This does **not** prove a real Docker ambiguous fault, real daemon alert delivery,
operator root-cause remediation, or later 9/9 partition completion.
