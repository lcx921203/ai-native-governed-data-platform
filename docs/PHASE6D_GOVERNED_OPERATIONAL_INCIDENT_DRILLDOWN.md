# Phase 6D — Governed Operational Incident Drilldown

## 1. Why Phase 6D exists

Phase 6C deliberately stops business-driver attribution when exact-partition operational health is unhealthy:

```text
metric anomaly
→ exact partition incomplete / overdue
→ DATA_PIPELINE_SUSPECTED
→ business attribution blocked
```

Phase 6D continues from that point and answers a narrower operational question:

```text
Which partition is incomplete?
Which consumer marts are missing?
Which run most recently failed?
What structured failure class/component/stage was persisted?
How many automatic recovery attempts are actually observed?
What would the existing Phase 3C recovery policy decide if evaluated now?
```

It does **not** introduce a new failure classifier or a new recovery policy.

---

## 2. Source-of-truth reuse

Phase 6D consumes the existing Phase 3C contracts:

```text
recovery_state.py
→ exact partition current truth
→ missing marts
→ run history
→ observed recovery attempts

failure_classification.py
→ structured failure class/source/component/reason/stage

recovery_policy.py
→ current bounded recovery decision
```

The governing rule is:

> **Observed runtime facts and policy decisions are different evidence types.**

For example:

```text
policy_action_if_evaluated_now = AUTO_REPLAY
```

does not mean an automatic replay has already been launched. Actual launches are counted only from persisted recovery-tagged Dagster runs.

---

## 3. Structured failure stage

Phase 3C already persisted:

```text
commerce/failure_class
commerce/failure_class_source
commerce/failure_component
commerce/failure_reason
```

Phase 6D adds one backward-compatible tag:

```text
commerce/failure_stage
```

The existing component contract remains unchanged:

```text
component = spark-thrift
```

while stage can distinguish:

```text
ingestion/shopify/load_fixtures.py
lakehouse/jobs/normalize_shopify_orders.py
```

For dbt:

```text
component = dbt:build
stage     = dbt:build
```

No free-text log parsing is used to invent a failure cause.

---

## 4. Incident evidence model

For every queried daily partition, Phase 6D can expose:

```text
partition_key
freshness_overdue
exact_partition_complete
missing_mart_asset_keys
run_ids
failed_run_ids
successful_run_ids
latest_failed_run
  ├── run_id
  ├── failure_class
  ├── failure_source
  ├── failure_component
  ├── failure_reason
  └── failure_stage
recovery
  ├── observed_auto_replay_attempts
  ├── active_run_ids
  ├── active_recovery_run_ids
  ├── policy_action_if_evaluated_now
  └── policy_reason
```

Absence of a failed run remains an absence of evidence. It does not prove a missed schedule and does not create a synthetic failure class.

---

## 5. Recovery semantics

Phase 6D calls the existing `decide_recovery()` policy. Therefore all Phase 3C invariants remain in force:

```text
partition complete
→ NO_ACTION

before freshness deadline
→ WAIT

active run owns partition
→ WAIT

infrastructure still unhealthy
→ ALERT_AND_WAIT

replay budget exhausted
→ ALERT_MANUAL

successful run but exact partition incomplete
→ ALERT_MANUAL

eligible newest no-run partition
→ bounded AUTO_REPLAY

transient / historical infrastructure failure + runtime healthy
→ bounded AUTO_REPLAY

deterministic code / data contract / unknown
→ fail closed / manual path
```

Phase 6D reports this as a **policy decision**, not an observed sensor action.

---

## 6. Integration with Phase 6C

The 6C pipeline-suspected branch is now:

```text
DATA_PIPELINE_SUSPECTED
→ skip Phase 6B business driver attribution
→ execute Phase 6D incident drilldown
→ project structured incident claims
→ answer through the existing Claim Ledger boundary
```

New response claim kinds:

```text
INCIDENT_EVIDENCE
RECOVERY_STATUS
```

A real runtime claim must still carry:

```text
RUNTIME_VERIFIED
```

---

## 7. Breadth and answer limits

One drilldown is bounded to at most seven daily partitions.

The answer layer emits detailed incident claims for at most three incomplete partitions; the full structured result may contain the remaining partitions and the answer explicitly declares the cap.

This prevents a broad incident window from overflowing the governed response envelope.

---

## 8. Runtime gate

Default:

```bash
PHASE6D_ALLOW_INCIDENT_DRILLDOWN=false
```

The live diagnostic-chain wrapper also requires:

```bash
PHASE6C_ALLOW_DIAGNOSTIC=true
```

Without the explicit gates, the live wrapper refuses with exit code `2`.

---

## 9. Evidence boundary

Static closure proves:

```text
contracts
provider wiring
Phase 3C policy reuse
failure-stage persistence contract
Claim Ledger projection
fail-closed gates
```

It does not prove:

```text
real Dagster Run Storage contents
real failed run tags
real missing mart materializations
real recovery sensor state
real automatic replay launch
```

Those remain **DEFERRED Runtime Acceptance**.
