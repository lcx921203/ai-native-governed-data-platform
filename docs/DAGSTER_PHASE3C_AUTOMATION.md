# Dagster Phase 3C — Automation Model（自动化模型）

## Current chain

```text
Schedule（调度）
→ Bounded Step Retry（有界步骤重试）
→ Freshness（数据新鲜度）
→ Recovery Candidate（恢复候选）
→ Exact Partition State Reader（精确分区状态读取）
→ Structured Failure Classification（结构化失败分类）
→ Recovery Decision（恢复决策）
→ Bounded Recovery Sensor（有界恢复传感器）
→ Exact Partition Replay（精确分区重放）
```

## Execution boundaries

```text
Deployment / model change
→ commerce_dbt_foundation_job
  ├─ seeds / master data / time spine
  └─ global Staging Views

Every completed UTC day
→ shopify_daily_partition_job
  ├─ Raw[day]
  ├─ Structured Source[day]
  └─ tag:shopify_windowed dbt assets[day]
```

Staging is a global View definition and does not pretend to own a daily data partition.

## Time contract

```text
00:00 UTC  previous source-update day closes
00:15 UTC  schedule starts latest completed partition
01:00 UTC  consumer Freshness deadline
```

The logical partition remains `[00:00, 24:00)`. The source-read start is expanded by a
5-minute lookback; the same effective window is passed to dbt as
`shopify_effective_start` / `shopify_effective_end`.

## Failure ownership

```text
Spark/runtime adapter
→ infrastructure_unavailable / transient_runtime / unknown

dbt command/artifact
→ deterministic_code / data_contract / unknown

Recovery Policy
→ consumes proven classes; never parses logs to invent meaning
```

## Retry ≠ Recovery

- Step Retry: max 2; cheap, inside one Run.
- Cross-run Auto Replay: max 1; requires deadline breach + exact state + replay-safe class.
- `UNKNOWN` may use bounded Step Retry but may not auto-replay across Runs.

## Acceptance state

Design / pure-policy acceptance is implemented with a hand-authored scenario oracle.
Real Dagster + Docker runtime evidence is still **DEFERRED**. See:

- `docs/DAGSTER_PHASE3C_ACCEPTANCE.md`
- `acceptance/phase3c/scenarios.py`
- `acceptance/phase3c/runtime_acceptance_plan.md`
