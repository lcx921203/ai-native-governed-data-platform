# Phase 3C · R09 — Run SUCCESS ≠ Partition Completeness

## Purpose

R09 proves that a Dagster Run status is not a substitute for the consumer-data contract.
The daily orchestration can end `SUCCESS` while the exact daily partition is still
incomplete from the consumer perspective.

```text
Dagster Run SUCCESS
        ≠
9/9 exact-partition consumer Mart materializations
```

The Recovery State Reader therefore evaluates both Run Storage and Asset Materialization
history.

## Fixed scenario

```text
partition = 2026-08-05
freshness deadline = 2026-08-06 01:00 UTC
R09 evaluation time = 2026-08-06 01:05 UTC

Daily Run = SUCCESS
orders                  = materialized
order_items             = materialized
payment_transactions    = materialized
refunds                 = materialized
refund_items             = materialized
fulfillments             = materialized
fulfillment_items        = materialized
fulfillment_events       = MISSING
```

Expected observation:

```text
successful_run = true
materialized   = false
missing_marts  = [fulfillment_events]
```

Expected decision:

```text
ALERT_MANUAL
successful_run_without_complete_partition
```

It must **not** become `missed_schedule_or_no_run`, because a successful owner Run exists.
It must also not auto replay: SUCCESS-with-missing-output is an internal consistency
anomaly whose cause is not yet proven replay-safe.

## Evidence layers

### R09-A — Local persistent Dagster event-store contract

`acceptance/phase3c/r09_success_incomplete_partition.py` creates a real local Dagster Run,
lets it finish SUCCESS, and emits seven partitioned `AssetMaterialization` events from
that same run.  The production State Reader and Sensor are then evaluated.

This proves:

- SUCCESS Run is visible from Run Storage;
- 8/9 exact-partition materialization history is visible from Event Storage;
- missing Mart is surfaced explicitly;
- policy fails closed to manual investigation;
- Sensor does not create an auto-recovery Run;
- replay budget remains zero.

### R09-B — Real pipeline inconsistency evidence

Still deferred.  A real runtime proof must capture an actual Daily Pipeline/DBT/Spark
condition where Run status and consumer completeness diverge, then preserve the Run ID,
materialization events, and root-cause investigation.

R09-A intentionally does not claim row-level Iceberg completeness.  Dagster materialization
presence is the orchestration contract; physical consumer-table correctness belongs to
Data Runtime acceptance.
