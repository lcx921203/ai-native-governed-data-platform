# Phase 3C R08 — Replay Budget Exhausted

## Purpose

R08 proves that automatic cross-run recovery is bounded.  Once automatic Recovery
attempt-1 has been persisted and then finishes unsuccessfully, the exact partition is
not eligible for attempt-2.

```text
historical failed daily run
        ↓
auto recovery attempt-1
        ↓
attempt-1 FAILURE
        ↓
active_run = false
auto_replay_attempts = 1
        ↓
Sensor evaluates again
        ↓
ALERT_MANUAL / auto_replay_budget_exhausted
        ↓
no attempt-2
```

The budget is intentionally derived from persisted Dagster runs tagged
`commerce/recovery=auto`; it is not an in-memory counter and is not incremented by
Sensor polling.  Therefore a process/daemon restart does not logically reset the replay
history as long as the same Run Storage remains authoritative.

## Decision precedence

R07 and R08 together define one state transition:

```text
attempt-1 active
    -> WAIT / active_run_owns_partition

attempt-1 finished unsuccessfully
    -> ALERT_MANUAL / auto_replay_budget_exhausted
```

The order in `decide_recovery()` is deliberate:

1. exact partition complete;
2. still inside freshness budget;
3. active owner;
4. current infrastructure health;
5. cross-run replay budget;
6. replay-safe failure class.

A replay-safe historical cause such as `transient_runtime` does not override an already
consumed automatic replay budget.

## R08-A fixed scenario

```text
partition = 2026-08-05
now       = 2026-08-06 01:20 UTC

Run A
  automation    = daily-schedule
  status        = FAILURE
  failure_class = transient_runtime

Run B
  automation       = recovery-sensor
  recovery         = auto
  recovery_attempt = 1
  status           = FAILURE
  failure_class    = transient_runtime

State Reader
  active_run          = false
  auto_replay_attempts = 1

Recovery Policy
  -> ALERT_MANUAL
  -> auto_replay_budget_exhausted

Recovery Sensor
  -> SkipReason
  -> no attempt-2 RunRequest
  -> exact-partition Run count unchanged
  -> automatic recovery Run count remains exactly 1
```

The attempt-1 failure is deliberately still `transient_runtime`: replay might normally
be useful, so the test proves that the bounded budget itself is what stops the loop.

## Runtime evidence still deferred

R08-A does **not** prove:

- a real Dagster daemon created attempt-1;
- attempt-1 actually ran against Docker/Spark and failed;
- an external alert/incident was delivered;
- an operator fixed the problem and completed a later manual replay.

Those remain real daemon / data-plane Runtime evidence.
