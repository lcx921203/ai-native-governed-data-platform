# Behavior Streaming Failure Drill

Status: **DEFINED / NOT EXECUTED IN THIS SOURCE PACKAGE**

这个演练专门验证“任务挂了以后为什么不丢 / 不重复最终结果”。
只有真正完成下面步骤并保存 Runtime Evidence 后，才能把 Exactly-once 从设计结论升级成运行验收结论。

## 前置检查

1. Kafka topic 有足够 retention，可以 Replay 最近 Checkpoint 之后的数据；
2. `FLINK_CHECKPOINT_STORAGE` 指向持久对象存储，而不是 TaskManager 本地临时目录；
3. Flink UI 已出现至少一个 `COMPLETED` Checkpoint；
4. Iceberg Sink 的最近成功 commit 时间持续更新。

## 演练步骤

```text
1. 启动 behavior job
2. 持续向 Kafka 写带唯一 event_id 的事件
3. 等待 Checkpoint N = COMPLETED
4. 记录 Kafka offset / Iceberg snapshot / product_view_5m 结果
5. kill 一个 TaskManager（不要优雅停止 Job）
6. 故障期间继续向 Kafka 写事件
7. 等待 Flink 按 Restart Strategy 恢复
8. 确认恢复点来自最近完成的 Checkpoint N
9. 等待新 Checkpoint 与 Iceberg commit 完成
10. 对账：canonical event_id 无重复、发送事件无缺失、窗口最终值正确
```

## 为什么故障后会重读一段 Kafka

Checkpoint N 之后、故障之前处理的数据可能已经经过 Operator，但 Checkpoint N+1 尚未成功，
因此恢复时 State 与 Source Position 一起回到 N。这段 Kafka 会 Replay。

这不是 Exactly-once 失败，而是 Exactly-once 的实现方式之一：
**允许物理重放，但保证恢复后的 Managed State 与支持 checkpoint-aware commit 的 Sink 最终只反映一次正确结果。**

## 排查指标

- latest completed checkpoint id / age
- checkpoint duration / size
- failed checkpoint count
- alignment duration / backpressure
- Kafka consumer lag
- RocksDB state size
- Iceberg last successful commit time

如果 Checkpoint 长期失败，不应该继续宣称“有 Checkpoint 所以一定不会丢”。
