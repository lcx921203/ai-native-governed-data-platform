# Phase 6F · Governed Approval Workflow & Audit Trail

## 1. Purpose

Phase 6E can say that a response action is `HUMAN_REQUIRED`. Phase 6F turns that governance boundary into a structured approval lifecycle without turning the Data Agent into a production execution engine.

```text
Phase 6D structured incident evidence
        ↓
Phase 6E advisory response plan
        ↓
Human-required action
        ↓
Phase 6F Approval Request
PENDING
  ├─ APPROVED
  ├─ REJECTED
  └─ EXPIRED
```

The governing invariant is:

```text
APPROVED ≠ EXECUTED
```

An approved request only means that a trusted human accepted the exact action/evidence package. Before any external execution, the execution authority must re-read current truth and confirm that the incident evidence and response action are still unchanged.

## 2. Sources of truth

Phase 6F does not invent recovery semantics.

- Phase 6D owns structured incident evidence.
- Phase 6E owns the advisory response plan and action authority.
- Phase 3C remains the recovery-policy / automation execution authority.
- Phase 6F owns only approval state and approval audit evidence.

Only Phase 6E steps whose `approval_boundary=HUMAN_REQUIRED` may enter this workflow. `AUTO_REPLAY` remains owned by the existing Dagster Recovery Sensor and does not create a human approval request.

## 3. Approval request identity

Each request binds all of the following:

```text
partition_key
response action
action authority
Phase 3C policy action / reason
exact Phase 6D partition incident evidence
exact Phase 6E partition response plan
request time
expiry time
```

The exact incident + response + step package is hashed into `evidence_fingerprint`. The immutable request itself has a second `request_hash`.

If the current incident or response plan changes after approval, the old approval remains part of history but becomes ineligible for use:

```text
APPROVED old evidence
        +
current evidence changed
        ↓
EVIDENCE_CHANGED
        ↓
external execution eligibility = false
```

## 4. State machine

Only `PENDING` may transition.

```text
PENDING → APPROVED
PENDING → REJECTED
PENDING → EXPIRED
```

`APPROVED`, `REJECTED`, and `EXPIRED` are terminal. Re-approval of a rejected or expired request is not allowed; a new governed request must be created from current incident truth.

## 5. Actor boundary

Approval/rejection requires an actor with:

```text
actor_type = HUMAN_OPERATOR
authenticated = true
identity_source = AUTHENTICATED_UPSTREAM
```

The workflow module does not authenticate users itself. Production identity must be injected by a trusted authenticated upstream service. A CLI string or LLM-provided name is not identity proof.

`AGENT` cannot approve or reject.

## 6. Audit trail

Each state transition is an append-only `ApprovalAuditEvent` with:

```text
sequence
approval_id
event_type
previous_status
new_status
occurred_at
actor
reason
request_hash
previous_event_hash
event_hash
```

Events form a SHA-256 hash chain. This catches in-record mutation during engineering validation, but it is **not** a digital signature, actor-authentication mechanism, or immutable production audit store.

`JsonlApprovalAuditStore` is an optional engineering adapter and requires:

```bash
PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE=true
```

That gate only permits approval-audit persistence. It never enables Dagster recovery or backfill execution.

## 7. External execution validation

An `APPROVED` request must pass `validate_for_external_execution(...)` immediately before an external execution service uses it.

The validation requires:

1. approval audit chain is intact;
2. status is `APPROVED`;
3. the same partition/action still exists in the current Phase 6E plan;
4. the current Phase 6D + 6E evidence fingerprint equals the approved fingerprint.

Success returns:

```text
ELIGIBLE_FOR_EXTERNAL_EXECUTION
eligible_for_external_execution = true
agent_execution_allowed = false
```

This is deliberately an authorization check, not an executor.

## 8. Runtime gates

```bash
PHASE6F_ALLOW_APPROVAL_WORKFLOW=false
PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE=false
```

The Phase 6F live wrapper also requires the Phase 6E response-planning gate. Production Dagster / manual-backfill execution remains outside this capability.

## 9. Static acceptance

Covered contracts include:

- human-required actions only;
- auto replay creates no human approval;
- agent self-approval rejected;
- unauthenticated actor rejected;
- terminal states immutable;
- pending expiry;
- evidence mutation invalidates previous approval;
- action disappearance invalidates previous approval;
- hash-chain mutation detection;
- audit persistence fails closed by default;
- approved state still reports `agent_execution_allowed=false`;
- no Dagster/backfill execution handle in the workflow module.

Real authenticated identity, production approval persistence, and downstream action execution remain `DEFERRED` until workstation/runtime acceptance.
