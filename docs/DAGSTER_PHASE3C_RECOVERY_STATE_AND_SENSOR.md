# Phase 3C — Recovery State Reader（恢复状态读取）& Sensor（传感器）

The Sensor is only an adapter:

```text
recent overdue partition
→ current exact 9-Mart materialization state
→ active / failed / successful Run history
→ current runtime health
→ pure Recovery Policy
→ at most one RunRequest(partition_key=...) per evaluation
```

Stable recovery Run Key:

```text
shopify-daily-recovery:<partition_key>:attempt-<n>
```

The sensor stays `STOPPED` by default until real Runtime Acceptance exists.
