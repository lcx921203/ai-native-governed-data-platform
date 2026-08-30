# Streaming Data Ingestion · 流式数据接入

日期：2026-08-20  
Runtime Evidence（真实运行证据）：**DEFERRED · 延期验收**

## 1. 三种数据接入模式

```text
Shopify SaaS API
  → Python GraphQL
  → Iceberg Observation（观察）

MySQL Item / Store
  → Flink CDC + Flink SQL
  → Iceberg Current State（当前状态）

Behavior Events
  → FastAPI Collector
  → Kafka
  → PyFlink DataStream
  → Iceberg Raw / Canonical / Realtime / Ops
```

这里的重点不是“多放几个组件”，而是让接入方式匹配源系统自己的变化语义。

## 2. MySQL Item / Store：Flink CDC + Flink SQL

生产假设 Item / Store 存储在内部 MySQL。Flink CDC 使用 `scan.startup.mode='initial'`：

```text
Initial Snapshot（初始快照）
        ↓
读取已有 Item / Store
        ↓
无缝切到 binlog
        ↓
持续接收 INSERT / UPDATE / DELETE
```

Item / Store 使用不同 `server-id` 范围，避免并行 CDC Reader 冲突。

CDC 输出 Changelog（变更日志）；Iceberg v2 当前状态表使用 Primary Key（主键）+ Upsert（更新写）接住更新与删除。

### 故障恢复

Flink SQL 模板显式开启 Exactly-once Checkpoint（精确一次检查点）。

Checkpoint 保存 CDC Source Position（源读取位置）以及需要恢复的托管状态。任务失败后从最近成功 Checkpoint 恢复，再继续读取对应 binlog 位置，而不是依赖 `updated_at` 猜恢复点。

## 3. Behavior Event：为什么使用 DataStream API

行为事件不是简单的数据搬运，它需要显式处理：

- Event Time（事件时间）
- Watermark（水位线）
- Out-of-order Event（乱序）
- Keyed State（键控状态）
- State TTL（状态过期）
- Window（窗口）
- Allowed Lateness（允许迟到）
- Side Output（旁路输出）
- Checkpoint（检查点）
- Restart / Recovery（重启 / 恢复）
- End-to-end Exactly-once（端到端精确一次）

因此行为链使用 PyFlink DataStream API，而不是把所有逻辑继续压进 Flink SQL。

## 4. 迟到数据：正常、可修正、Too-late 三段

项目的 5 分钟 `product_view` Event-time Window 默认：

```text
Bounded Out-of-Orderness = 2 min
Allowed Lateness         = 5 min
```

```text
Watermark < window_end
    → 正常进入窗口

window_end < Watermark <= window_end + 5m
    → 迟到但仍允许修正
    → Late Firing（迟到触发）
    → 更新同一个实时窗口结果

Watermark > window_end + 5m
    → Too-late（过度迟到）
    → Side Output
    → ops.behavior_event_too_late
```

`realtime.product_view_5m` 因此按 `(item_id, window_start, window_end)` UPSERT，同一个窗口的 Late Firing 不会 Append 成重复结果。

### Invalid / DLQ 与 Too-late 必须分开

Invalid / DLQ（无效数据 / 死信）代表 JSON 损坏、必填字段缺失、`event_time` 非法等 Data Contract（数据契约）问题。

Too-late 是完全合法的业务事件，只是超过实时修正预算。Canonical 明细仍然保留，Too-late 另外登记到 Ops 表，后续 dbt / Batch Reconciliation（批处理校正）可以重算权威聚合。

### Idle Partition（空闲分区）

某个 Kafka Partition 长时间没有事件时，一个旧 Watermark 不应该永久拖住整个并行 Job。因此 DataStream Job 使用 `with_idleness()`。

## 5. State、Checkpoint、Savepoint

**State（状态）**是作业现在“记着什么”，例如某个 `event_id` 是否已经处理、当前窗口 count、Window / Timer 内部状态。

项目使用 `event_id` 做 Keyed State 去重，并配置 State TTL，避免状态无限增长。

**Checkpoint（检查点）**是系统周期性生成的一致性恢复点，包含 Source Position + Managed State。任务失败后从最近成功 Checkpoint 恢复。

**Savepoint（保存点）**更适合升级、迁移、改并行度等计划性操作，不是日常故障恢复 Checkpoint 的同义词。

## 6. Exactly-once 到底是什么意思

Exactly-once **不是**“一条消息物理上永远只执行一次”。

```text
Checkpoint 100 成功
↓
继续处理 Kafka 10001 ~ 10500
↓
Checkpoint 101 尚未成功
↓
TaskManager 挂掉
↓
Restore Checkpoint 100
↓
恢复 Kafka Position + Managed State
↓
10001 ~ 10500 允许 Replay（重放）
```

关键是恢复后的 State 和最终 Sink Result（写入结果）与无故障执行等价。

所以 Flink 内部 Exactly-once 还不等于端到端 Exactly-once；Sink 也必须参与 Checkpoint Commit。

本项目使用 Iceberg Flink Sink；Raw 表保持 append-only，Current / Canonical / Window 表按各自业务键使用 Iceberg v2 Upsert。

## 7. Backpressure 与 Unaligned Checkpoint

Unaligned Checkpoint（非对齐检查点）不是默认应该开启的“高级功能”。

当严重 Backpressure（反压）导致 Barrier Alignment（屏障对齐）过慢时，可以在真实指标支持下按需开启。本项目默认关闭。

## 8. Failure Drill · 故障演练

源码已经定义：

```text
启动 Kafka / Flink / Iceberg
↓
持续发送行为事件
↓
等待 Checkpoint N 成功
↓
Kill TaskManager
↓
继续发 Kafka
↓
Flink 自动恢复
↓
验证 no loss / no unexpected duplicate / State restored / Iceberg commit continues
```

详见 `ingestion/behavior/flink/failure_drill.md`。

当前状态：

```text
DEFINED / NOT EXECUTED
已定义 / 未执行
```

因此真实 MySQL / Kafka / Flink / Iceberg Runtime 仍然是 DEFERRED。
