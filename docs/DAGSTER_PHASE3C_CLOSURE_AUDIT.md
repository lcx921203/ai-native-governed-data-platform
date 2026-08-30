# Phase 3C Closure Audit — Automation Model

## Scope

This audit closes the **engineering/static** Phase 3C implementation after R01–R13.
It does **not** upgrade any real Dagster/dbt/Docker Runtime Acceptance item from
`DEFERRED` to `PASS`.

The closure question is not “did we add every possible automation feature?” It is:

> Are Schedule, Freshness, Retry, Failure Classification, Recovery State, Recovery
> Policy, Sensor, exact-partition completeness, duplicate protection and replay budget
> internally consistent, executable from source, and protected against known regressions?

## Final architecture contract

```text
00:15 UTC Schedule (explicitly STOPPED until runtime acceptance)
        ↓
Daily exact partition
        ↓
Raw / Structured Source / dbt window-aware compute
        ↓
Bounded Step Retry (only proven retry-safe classes, max_retries=2)
        ↓
01:00 UTC consumer Freshness deadline
        ↓
Overdue candidate gate
        ↓
Exact-partition current state
        ↓
Structured failure evidence + current runtime health
        ↓
Bounded Recovery Decision
        ↓
Recovery Sensor (explicitly STOPPED until runtime acceptance)
        ↓
At most one automatic cross-run replay
```

## Closure findings and fixes

### C01 — Missing Asset Check compute scripts — FIXED

`checks/lakehouse.py` referenced two Spark jobs that did not exist:

- `lakehouse/jobs/check_raw_observations.py`
- `lakehouse/jobs/check_source_idempotency.py`

Both are now real source files. The checks pass the same Dagster effective source window
used by Raw/Normalize. A closure test scans production `spark_submit(...)` literals and
fails if a referenced script is missing.

The Raw check validates observations in the half-open interval `[window_start, window_end)`.
The Structured Source check validates `Business Key + record_hash` uniqueness for Business
Versions whose observation interval overlaps the execution window. The overlap predicate is
intentional: a Business Version may be re-observed later, moving `last_source_updated_at`
forward; replaying an older partition must not hide that version from the check.

These custom checks remain **non-blocking** observability/control checks. dbt-owned semantic
Data Contract failure remains the R05 blocking/manual-escalation path.

### C02 — Schedule/Freshness time contract had duplicate truths — FIXED

Previously `00:15`, `01:00`, cron `0 1 * * *`, `45 minutes`, and the partition deadline
were partly independent literals. The canonical contract now owns schedule hour/minute and
deadline hour/minute once, and derives:

- Freshness cron
- Freshness lower-bound/service budget
- Exact partition deadline

This prevents a future schedule/deadline change from silently leaving the Recovery oracle on
an old time.

### C03 — `MISSED_SCHEDULE` was a dead FailureClass — FIXED

A missed schedule is not a failed execution. In this design it is proven by:

```text
no run owner
+ exact partition incomplete
+ Freshness overdue
+ newest overdue partition eligibility
```

No production layer emitted `failure_class=missed_schedule`, so the enum member and explicit
failure-class recovery branch were dead and semantically misleading. They are removed.

### C04 — Recovery safety state was capped at 50 runs — FIXED

Replay budget is reconstructed from persisted Run Storage. Capping exact-partition history at
50 could theoretically hide an older automatic recovery and incorrectly reset the budget.
The production State Reader now reads the full matching run history and makes descending
`id` ordering explicit.

### C05 — Schedule activation state was implicit — FIXED

The Recovery Sensor was already explicitly `STOPPED`, but the Schedule relied on Dagster's
default. The Schedule now explicitly declares `DefaultScheduleStatus.STOPPED` too.

Activation is therefore an operational Runtime-Acceptance action, not an accidental side
effect of loading Definitions.

### C06 — Job-name contract duplicated — FIXED

The production Daily Job now uses `SHOPIFY_DAILY_JOB_NAME`, the same constant used by the
State Reader. This prevents Recovery queries from silently looking for a stale job name after
future refactoring.

## Test taxonomy

The test count must not be interpreted as one homogeneous evidence level.

### Pure policy tests

Validate deterministic decision rules without Dagster/dbt/Docker.

### Static/source wiring tests

Validate AST/source wiring, referenced files, time contracts and fail-closed invariants.
They are especially important while Dagster is unavailable, but they do not execute a daemon.

### Runtime harnesses R01–R13

Executable programs are present for loaded ScheduleDefinition/SensorDefinition/persistent
Dagster Event/Run Storage/dbt/Docker scenarios. In this environment they remain unexecuted
where Dagster/dbt/Docker packages are unavailable.

### Real runtime evidence — still DEFERRED

Still required before claiming Phase 3C Runtime PASS:

- real `dagster definitions validate`
- real Schedule daemon tick
- real Freshness timing/service evidence
- real Sensor ticks and run-key deduplication
- real Docker infrastructure outage/restore
- real Spark timeout/retry events
- real dbt Data Contract and deterministic-code failures
- real recovery Run creation
- same-run 9/9 exact-partition Mart materializations
- physical Spark/Iceberg scan/pruning evidence where applicable

## Accepted closure tradeoff

R01–R13 acceptance programs intentionally contain some repeated seed/query helpers. They are
kept self-contained so each scenario can be run and read independently. Production policy and
state logic remain centralized. Refactoring acceptance helpers during closure would increase
regression risk without changing production correctness, so it is intentionally deferred.

## Static closure command

```bash
./infra/runtime/run_phase3c_static_closure.sh
```

This runs pure/static/source contracts, the 13-scenario Recovery Oracle, Python compilation,
and shell syntax checks. A PASS means **Phase 3C Engineering/Static Closure PASS only**.

For full runtime preflight when Dagster/dbt/Docker are available:

```bash
./infra/runtime/run_phase3c_dagster_preflight.sh
```
