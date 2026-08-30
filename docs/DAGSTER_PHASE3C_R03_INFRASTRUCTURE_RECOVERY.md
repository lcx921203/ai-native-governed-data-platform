# Phase 3C · R03 Infrastructure Outage → Recovery

## Story

```text
00:15 Schedule creates exact-partition Run
        ↓
spark-thrift unavailable
        ↓
asset attempt #1 fails
        ↓
Step Retry #1
        ↓
Step Retry #2
        ↓
Run FAILURE
commerce/failure_class=infrastructure_unavailable
        ↓
01:00 Freshness deadline passes
        ↓
if infrastructure still down
    → ALERT_AND_WAIT
        ↓
after infrastructure is restored
    → AUTO_REPLAY attempt-1
        ↓
exact same partition
        ↓
9/9 consumer marts
```

## Important distinction

Historical cause and current recoverability are separate facts:

```text
historical failure_class = infrastructure_unavailable
current runtime health    = unhealthy
→ do not replay yet

historical failure_class = infrastructure_unavailable
current runtime health    = healthy
→ one bounded replay is allowed
```

The Sensor does not rewrite the historical failure class. It asks whether the current
runtime is healthy enough to safely retry the exact partition.

## R03 evidence levels

```text
R03-A1  local Dagster adapter/retry probe
        production SparkComposeResource
        + production retry count (2)
        + simulated stopped spark-thrift
        → 3 total attempts
        → final Run FAILURE
        → infrastructure_unavailable run tag

R03-A2  local persistent Dagster state + real Recovery SensorDefinition
        failed exact-partition run record
        + runtime still down
        → ALERT_AND_WAIT / no RunRequest

        same historical run
        + runtime restored
        → AUTO_REPLAY attempt-1 / exact partition

R03-B   real Docker outage
        stop spark-thrift before real daily run
        → real retry events + real failure tags

R03-C   real daemon recovery after restore
        → one recovery Run committed

R03-D   recovery data runtime
        → recovery run itself materializes 9/9 exact-partition marts
```

R03-A deliberately uses zero retry delay so acceptance does not sleep through the
production exponential backoff. It copies the production `max_retries=2`; the real
backoff timing is reserved for R03-B.
