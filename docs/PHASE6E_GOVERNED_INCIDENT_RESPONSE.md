# Phase 6E — Governed Incident Response Planner + Human Approval Boundary

## Objective

Phase 6D answers **what is broken and what Phase 3C recovery policy would decide now**.
Phase 6E answers **what should happen next, who owns that action, and where the Agent must stop**.

The central rule is:

```text
Incident evidence
→ Phase 3C recovery decision
→ advisory response plan
→ authority / approval boundary

never:
Agent → direct Dagster replay/backfill write
```

Phase 6E has no Dagster write handle and no backfill executor.

## Control flow

```text
Phase 6C
DATA_PIPELINE_SUSPECTED
        ↓
Phase 6D
RUNTIME_VERIFIED structured incident evidence
        ↓
Phase 6E
GovernedIncidentResponsePlanner
        ↓
Phase 3C action = ?

NO_ACTION
→ close only when exact partition is complete

WAIT
→ active owner exists: wait for that owner
→ within freshness budget: do not intervene

AUTO_REPLAY
→ delegate to existing Dagster Recovery Sensor
→ Agent does not launch the replay

ALERT_AND_WAIT
→ human/platform operator restores infrastructure
→ re-read current truth
→ re-evaluate Phase 3C policy

ALERT_MANUAL
→ repair / investigate
→ human approval boundary
→ explicit manual backfill only after validation
→ verify exact partition completion
```

## Authority model

Phase 6E distinguishes three things that must not be collapsed:

```text
Observed runtime fact
!=
Phase 3C policy decision
!=
Actual execution
```

`AUTO_REPLAY` means the existing Phase 3C policy permits the bounded replay. It does not mean a new recovery run has already been created, and it does not grant the Agent permission to create one.

Authorities used by the response plan:

- `DAGSTER_RECOVERY_SENSOR`: existing automatic recovery owner;
- `DAGSTER_RUN_OWNER`: current normal run already owns the partition;
- `HUMAN_DATA_OPERATOR`: manual data repair/backfill authority;
- `PLATFORM_OPERATOR`: runtime infrastructure restoration authority;
- `NONE`: no intervention is authorized yet.

Every `IncidentResponseStep` persists:

```text
executable_by_agent = false
```

## Human approval cases

### Data contract failure

```text
data_contract
→ inspect failed contract/test
→ repair or quarantine invalid data
→ re-run validation
→ human approval for manual backfill
→ exact-partition verification
```

Repeating the same failed partition before fixing the contract is not an allowed response.

### Deterministic code failure

```text
deterministic_code
→ fix code
→ validate/deploy correction
→ human approval for manual backfill
→ exact-partition verification
```

### Auto-replay budget exhausted

```text
auto_replay_attempts >= budget
→ investigate repeated failure
→ no automatic second replay
→ explicit human decision
```

### Unknown failure

```text
unknown
→ establish a structured cause
→ no replay-safe assumption
→ fail closed
```

### Historical no-run gap

Absence of an old run is not evidence that the schedule was missed. Historical no-run gaps therefore remain explicit manual-backfill decisions.

## Infrastructure failure

When Phase 3C returns:

```text
ALERT_AND_WAIT / infrastructure_unhealthy
```

Phase 6E produces:

```text
RESTORE_INFRASTRUCTURE
→ PLATFORM_OPERATOR / HUMAN_REQUIRED

REEVALUATE_RECOVERY_POLICY
→ HUMAN_DATA_OPERATOR / HUMAN_REQUIRED
```

It does **not** pre-authorize replay after infrastructure recovery. Current partition truth and current policy are read again first.

## Existing owner protection

When an active recovery run already owns the partition:

```text
WAIT_FOR_ACTIVE_RECOVERY
→ no duplicate replay
→ verify exact partition after completion
```

This preserves the Phase 3C owner-before-budget rule.

## Evidence response

Phase 6E adds two Claim Ledger kinds:

```text
INCIDENT_RESPONSE_PLAN
ACTION_AUTHORITY
```

The response can explain the governed recommendation and the owner/approval boundary, but the response itself is not an execution request.

## Runtime gates

```bash
PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING=false
PHASE6D_ALLOW_INCIDENT_DRILLDOWN=false
PHASE6C_ALLOW_DIAGNOSTIC=false
```

Even when the planning gate is enabled, `writes_enabled: false` remains part of the Phase 6E policy contract.

## Main files

```text
agent/incident_response/contracts.py
agent/incident_response/planner.py
agent/incident_response/response.py
agent/contracts/incident_response_policy.yml
tests/test_phase6e_incident_response_planner.py
infra/runtime/run_phase6e_incident_response_static.sh
infra/runtime/run_phase6e_incident_response_live.sh
```

## Runtime boundary

Static closure proves policy mapping, approval boundaries, duplicate-owner protection, evidence projection and the absence of Agent execution authority.

It does not prove a real Sensor-created recovery run, a human-approved backfill, a real infrastructure repair, or exact-partition completion after remediation. Those remain runtime acceptance items.
