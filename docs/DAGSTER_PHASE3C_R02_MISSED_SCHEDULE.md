# Phase 3C · R02 Missed Schedule Acceptance

## The failure has no failed Run

A missed schedule is different from an execution failure:

```text
00:15 schedule should launch
        ↓
(no Dagster Run exists)
        ↓
01:00 freshness deadline passes
        ↓
exact partition remains incomplete
```

There is no failed run to classify. Recovery starts from consumer state plus the
absence of a current owner.

## Safety boundary: no run does not automatically mean missed schedule

A newly deployed Dagster instance may have no historical runs at all. Therefore the
recovery sensor only treats the **newest overdue partition** as eligible for automatic
no-run recovery. Older no-run gaps fail closed:

```text
newest overdue + no run + incomplete + infra healthy
    → AUTO_REPLAY once

older historical gap + no run
    → ALERT_MANUAL / explicit backfill
```

This keeps the seven-day recovery horizon useful for partitions with real historical
failure evidence without silently converting deployment history into backfill work.

## R02 evidence levels

```text
R02-A  persistent temporary Dagster instance
       + real SensorDefinition invocation
       + fixed clock / healthy runtime adapter
       → one bounded RunRequest

R02-B  real daemon tick
       → RunRequest committed / real run created
       → stable run_key prevents duplicate real runs

R02-C  recovery data runtime
       → same recovery run completes exact partition 9/9 marts
```

R02-A intentionally does not launch the job. It proves that the real state reader,
recovery policy and SensorDefinition agree on the missed-schedule decision.
