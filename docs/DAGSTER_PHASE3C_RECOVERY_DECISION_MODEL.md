# Phase 3C — Recovery Decision Model（恢复决策模型）

```text
exact partition complete                         → NO_ACTION
before Freshness deadline                        → WAIT
active Run already owns partition                → WAIT
current infrastructure unhealthy                 → ALERT_AND_WAIT
auto replay budget exhausted                     → ALERT_MANUAL
Run SUCCESS but exact consumer partition missing → ALERT_MANUAL
no Run owned overdue partition                   → AUTO_REPLAY once
transient failure + runtime recovered             → AUTO_REPLAY once
historical infrastructure failure + recovered    → AUTO_REPLAY once
deterministic code / data contract / unknown     → ALERT_MANUAL
```

Historical Failure Cause（历史失败原因） ≠ Current Recoverability（当前可恢复性）.
