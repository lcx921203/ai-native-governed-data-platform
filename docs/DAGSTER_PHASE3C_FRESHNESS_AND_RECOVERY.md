
## 2026-08-20 post-baseline SLA extension

The historical Phase 3C frozen source closed with eight consumer Marts. The current source baseline preserves those frozen bytes and extends the governed consumer SLA through `consumer_sla.py` to **nine Marts**, adding `order_lifecycle_snapshot`. `recovery_state_current.py` is the current exact-partition reader used by Freshness/Recovery runtime wiring. This is SOURCE/STATIC evolution only; real nine-Mart Dagster/Iceberg Runtime evidence remains DEFERRED.

# Phase 3C — Freshness（数据新鲜度）& Recovery（恢复）

Schedule（调度计划）回答“什么时候开始”；Freshness（数据新鲜度）回答“最晚什么时候必须有结果”。

Current learning contract:

```text
00:15 UTC schedule
01:00 UTC consumer deadline
45 minutes service budget
```

Freshness is applied at the nine consumer-facing Business Marts. Service Freshness
（服务新鲜度） ≠ Exact Partition Completeness（精确分区完整性）; the Recovery State
Reader checks exact-partition materialization separately.
