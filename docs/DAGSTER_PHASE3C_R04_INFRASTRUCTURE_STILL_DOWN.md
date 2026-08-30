# Phase 3C R04 — Infrastructure Still Down

## Question

R03 proves that an infrastructure failure can become replayable after the runtime
recovers. R04 proves the inverse safety property: while the runtime is still down,
repeated Recovery Sensor ticks must not create runs or consume the one allowed
automatic replay attempt.

## Fixed acceptance story

```text
partition              = 2026-08-05
consumer deadline       = 2026-08-06 01:00 UTC
historical run          = FAILURE
failure_class           = infrastructure_unavailable

01:05 current infra down -> ALERT_AND_WAIT
01:10 current infra down -> ALERT_AND_WAIT
01:15 current infra down -> ALERT_AND_WAIT

No Recovery Run exists during those waits.
auto_replay_attempts remains 0.

01:20 current infra healthy
-> AUTO_REPLAY
-> shopify-daily-recovery:2026-08-05:attempt-1
```

## Budget invariant

The Recovery Sensor does not own an in-memory retry counter. The exact-partition state
reader derives `auto_replay_attempts` from persisted Dagster runs tagged:

```text
commerce/recovery = auto
```

A Sensor `SkipReason` therefore cannot consume recovery budget. Only a recovery run
that was actually created in run storage counts against the cross-run replay budget.
This is intentional: polling while an external dependency is unavailable must be
idempotent.

## R04-A — Local persistent Dagster acceptance

`acceptance/phase3c/r04_infrastructure_still_down.py` seeds one failed daily run and
evaluates the production Recovery Sensor three times against the same persistent local
Dagster instance with current infrastructure forced unhealthy.

It proves:

- all three evaluations stay `ALERT_AND_WAIT / infrastructure_unhealthy`;
- every evaluation returns `SkipReason`, not `RunRequest`;
- exact-partition run count is unchanged;
- `auto_replay_attempts == 0` before and after every wait tick;
- after health changes to true, the first recovery request is still `attempt-1`.

It does **not** prove a real daemon polling loop or a real Docker outage.

## R04-B — Real daemon / Docker evidence

In the real runtime:

1. stop `spark-thrift`;
2. preserve it as down across at least three real Sensor intervals;
3. save Sensor Tick evidence showing no recovery Run is created;
4. verify no `commerce/recovery=auto` run exists for the partition;
5. restore `spark-thrift`;
6. verify the next replay-safe Sensor Tick creates only `attempt-1`.

R04 is Runtime PASS only after those daemon/run-storage events are saved.
