# Phase 3C R11 — Freshness Guard

## Purpose

R11 proves that **incomplete does not mean recoverable yet**.

For the `2026-08-05` daily partition:

```text
00:15 UTC  normal Schedule tick
00:40 UTC  partition may still be incomplete
01:00 UTC  consumer Freshness deadline
```

At `00:40`, Recovery has no permission to intervene merely because the 9 consumer Mart
materializations are not complete.

## Contract

Two gates protect the partition before the deadline.

### 1. Recovery Policy gate

```text
materialized = false
freshness_overdue = false
        ↓
WAIT
within_freshness_budget
```

This check intentionally happens before Active Owner, current infrastructure health,
replay budget, and replay-safe failure-class branches.

### 2. Recovery Sensor candidate gate

The production Sensor begins with:

```python
candidate_keys = overdue_partition_keys(now_utc)
```

At `2026-08-06 00:40 UTC`, `2026-08-05` is not in that candidate set. At exactly
`2026-08-06 01:00 UTC`, it becomes eligible for recovery evaluation.

So the system does not need to continuously evaluate an incomplete not-yet-overdue
partition and then remember not to recover it; the partition is excluded before State
Reader evaluation.

## R11-A local acceptance

`acceptance/phase3c/r11_freshness_guard.py` uses a persistent temporary Dagster instance:

1. persist a normal active Daily Run for `2026-08-05`;
2. keep older genuinely-overdue partitions complete so they cannot distract the Sensor;
3. evaluate the target partition at `00:40` with `freshness_overdue=false`;
4. require `WAIT / within_freshness_budget`;
5. run the production Recovery Sensor at the same fixed time;
6. prove no automatic Recovery Run is persisted;
7. prove the target enters the recovery candidate set only at the `01:00` deadline.

## What this does not prove

R11-A does **not** prove:

- a real Dagster daemon tick occurred at 00:40 UTC;
- Dagster preview Freshness evaluation emitted real runtime evidence;
- a real dbt/Spark Daily Run was incomplete at 00:40;
- the Iceberg consumer partition later completed 9/9.

Those remain real Runtime/Data evidence.
