# Behavior Event Streaming

这条生产链专门体现 Flink 的 Stateful Streaming（有状态流处理）能力：

```text
Web / App Tracking SDK
        │ HTTPS
        ▼
FastAPI Event Collector
        │ Kafka Producer
        ▼
commerce.behavior.events
        │
        ▼
PyFlink DataStream API
  ├─ JSON / Schema Validation
  ├─ Event Time + Watermark
  ├─ Keyed State Dedup + TTL
  ├─ 5-minute Event-time Window
  ├─ Allowed Lateness
  ├─ Too-late Side Output
  ├─ Invalid Side Output / DLQ
  ├─ Checkpoint + Recovery
  └─ Iceberg exactly-once sink contract
```

## 为什么不用 Flink SQL 把所有事情都做完

MySQL CDC 主要展示 Changelog，所以用 Flink SQL 更清楚；行为事件这一条刻意用 DataStream API，
因为要把 Watermark、State、Timer、Side Output、Checkpoint 和故障恢复真正写进代码。

## 两类旁路流一定要分开

- **invalid / DLQ**：JSON 解析失败、必填字段缺失、时间格式错误，是数据契约问题；
- **too-late**：业务事件本身合法，只是超过实时窗口允许修正的时间预算。

Too-late 不是坏数据。它会落 `ops.behavior_event_too_late`，之后由离线 reconciliation / dbt
重新计算权威聚合，不能直接把合法事件扔掉。

## 默认时间策略

- bounded out-of-orderness: 2 minutes
- idle partition timeout: 1 minute
- 5-minute tumbling window
- allowed lateness: 5 minutes
- event-id dedup state TTL: 24 hours

这些值是项目的初始生产策略，不是 Flink 的固定答案；真实环境应根据事件延迟分布和业务 SLA 调整。

## Exactly-once 语义

这里的 Exactly-once 不是“物理上每条事件只执行一次”。故障发生后，最近成功 Checkpoint 之后的 Kafka
记录可能 Replay（重放）。目标是：恢复后的 Flink State + Source Position + Iceberg Commit 最终等价于
没有发生故障时的一次正确结果。

## Runtime Evidence

当前源码完成 production-oriented engineering；真实 Kafka / Flink / Iceberg 故障演练仍是 DEFERRED。
详见 `failure_drill.md`。


## 迟到数据的三段处理

以 5 分钟 `product_view` Event-time Window 为例：

1. Watermark 还没越过 `window_end`：正常进入当前窗口；
2. Watermark 已越过 `window_end`，但还没超过 `window_end + allowed_lateness`：
   仍接收，并触发 late firing。`realtime.product_view_5m` 必须按窗口主键 UPSERT，
   用更新后的 count 覆盖同一窗口旧结果；
3. Watermark 已越过 `window_end + allowed_lateness`：进入 `TOO_LATE_TAG` Side Output，
   落 `ops.behavior_event_too_late`。事件本身仍是合法数据，而且 canonical 明细已经落
   `source.behavior_event`，后续离线/dbt reconciliation 可以重算权威指标。

`invalid` 与 `too-late` 不能混为一谈：前者是结构/契约错误，后者是合法业务事件超过实时修正预算。

`with_idleness()` 也很重要：如果某个 Kafka Partition 长时间没有事件，不应让它的旧
Watermark 永久拖住整个并行流的全局 Event-time 进度。
