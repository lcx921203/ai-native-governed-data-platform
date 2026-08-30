# Phase 3C R07 — Duplicate Recovery Guard

## Purpose

R07 proves that persistence of the first automatic recovery run immediately gives that
run ownership of the exact daily partition. A later Recovery Sensor evaluation must not
create a second owner while the first run is still active.

```text
historical failed daily run
        ↓
recovery attempt-1 persisted
        ↓
status = NOT_STARTED / QUEUED / STARTING / STARTED / MANAGED / CANCELING
        ↓
next Sensor evaluation
        ↓
active_run = true
        ↓
WAIT / active_run_owns_partition
        ↓
no second RunRequest
```

Ownership begins at persisted `NOT_STARTED`; execution does not need to have started.
This closes the race window between Run creation and actual executor pickup.

## Important precedence

A persisted automatic recovery run already counts toward the bounded replay budget:

```text
auto_replay_attempts = 1
```

However, while that run is active the decision must be:

```text
active_run_owns_partition
```

not:

```text
auto_replay_budget_exhausted
```

The ordering is intentional:

1. exact partition already complete → NO_ACTION;
2. freshness deadline not breached → WAIT;
3. active run owns partition → WAIT;
4. current infrastructure health;
5. replay budget;
6. failure-class-specific recovery.

Once attempt-1 is no longer active and remains failed/incomplete, R08 will prove that
the same persisted attempt count now causes `auto_replay_budget_exhausted`.

## Two duplicate-protection layers

### Layer A — application ownership guard

`collect_partition_recovery_state()` reads exact-partition runs from Dagster Run Storage.
Any run in an active status makes `observation.active_run=true`. The Recovery Policy
then returns `WAIT / active_run_owns_partition` before it considers another replay.

R07-A proves this layer using a persistent temporary Dagster instance and the production
Recovery SensorDefinition.

### Layer B — Dagster sensor run-key deduplication

The Recovery Sensor also emits a stable key:

```text
shopify-daily-recovery:<partition>:attempt-<n>
```

Dagster's daemon uses a sensor RunRequest `run_key` as a uniqueness key for that sensor,
so repeated requests with an already-used key are not turned into duplicate runs.
This is an additional framework-level guard, but real daemon persistence/dedup remains a
runtime evidence item rather than something R07-A claims to prove.

## R07-A fixed scenario

```text
partition = 2026-08-05
now       = 2026-08-06 01:10 UTC

Run A
  automation    = daily-schedule
  status        = FAILURE
  failure_class = transient_runtime

Run B
  automation       = recovery-sensor
  recovery         = auto
  recovery_attempt = 1
  status           = NOT_STARTED (active owner)

Sensor evaluates again
  → policy WAIT
  → reason active_run_owns_partition
  → SkipReason
  → partition run count unchanged
  → automatic recovery run count remains exactly 1
  → auto_replay_attempts remains 1
```

## Runtime evidence still deferred

R07-A does **not** prove:

- a real daemon created attempt-1 from a prior Sensor RunRequest;
- the daemon rejected a duplicate request by stable `run_key`;
- attempt-1 executed successfully;
- 9/9 exact-partition marts were materialized.

Those require real Dagster daemon / Docker runtime evidence.
