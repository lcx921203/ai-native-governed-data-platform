# Phase 3C · R01 Normal Schedule Acceptance

## Why R01 is split

`2026-08-05` is the fixed teaching partition. Its real schedule tick was
`2026-08-06 00:15 UTC`, so a current daemon cannot recreate that historical tick.
R01 therefore has three evidence layers:

```text
R01-A  real ScheduleDefinition evaluation at fixed historical time
R01-B  real future daemon tick
R01-C  same-run exact-partition 9/9 Mart completion before deadline
```

This prevents two false positives:

1. manually replaying `2026-08-05` and calling it a schedule run;
2. combining materializations from several old runs and calling them one complete run.

## Acceptance identity

For a partition `D`:

```text
expected schedule tick = D + 1 day at 00:15 UTC
consumer deadline      = D + 1 day at 01:00 UTC
freshness budget       = 45 minutes
```

The live verifier requires the schedule-origin tag:

```text
commerce/automation = daily-schedule
```

and then checks all nine mart materializations against the exact same `run_id`.
