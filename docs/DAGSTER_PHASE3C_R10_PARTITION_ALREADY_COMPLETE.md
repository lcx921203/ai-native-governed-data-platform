# Phase 3C R10 — Partition Already Complete

## Purpose

R10 proves that recovery is driven by **current exact-partition state**, not stale Run
history.

A historical Daily Run may still be recorded as `FAILURE`, but if a later repair,
backfill, or operator action has already produced all 9 consumer Mart materializations
for the exact partition, there is nothing left for automatic recovery to repair.

```text
historical Run FAILURE
    +
current 9/9 exact-partition Marts
    ↓
NO_ACTION
partition_already_materialized
```

## Decision precedence

`decide_recovery()` checks `observation.materialized` before freshness, active ownership,
current infrastructure health, replay budget, successful/failed Run history, or failure
class. This is deliberate: once the consumer contract is already complete, historical
execution problems no longer grant recovery permission.

## R10-A local evidence

`acceptance/phase3c/r10_partition_already_complete.py` uses a persistent temporary
Dagster instance to:

1. persist a failed `shopify_daily_partition_job` run for `2026-08-05`;
2. execute an independent `r10_manual_repair_job`;
3. emit `AssetMaterialization` events for all 9 consumer Mart asset keys and the same
   exact partition;
4. read the state through the production `collect_partition_recovery_state()`;
5. evaluate the production recovery policy;
6. evaluate the production recovery SensorDefinition.

Required result:

```text
failed_run=true
materialized=true
missing_mart_asset_keys=()
failure_class=transient_runtime

→ NO_ACTION / partition_already_materialized
→ Sensor SkipReason
→ no automatic recovery Run
```

## Evidence boundary

R10-A proves Dagster Run/Event Storage and orchestration-policy behavior. It does **not**
prove that Iceberg row-level data is complete, nor that a real dbt/Spark manual backfill
successfully repaired the partition. Those remain R10-B Runtime/data evidence.
