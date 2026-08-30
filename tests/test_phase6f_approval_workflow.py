from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from agent.approval_workflow import (
    ApprovalActor,
    ApprovalActorType,
    ApprovalAuditWriteRefused,
    ApprovalAuthorizationStatus,
    ApprovalStatus,
    ApprovalTransitionError,
    ApprovalWorkflowEvidenceComposer,
    ApprovalWorkflowStatus,
    GovernedApprovalWorkflow,
    JsonlApprovalAuditStore,
)
from agent.incident_drilldown import (
    FailedRunEvidence,
    IncidentDrilldownResult,
    IncidentDrilldownStatus,
    PartitionIncidentEvidence,
    RecoveryPolicySnapshot,
)
from agent.incident_response import GovernedIncidentResponsePlanner, IncidentResponseStatus
from agent.response import ClaimKind, render_deterministic, validate_answer_draft

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)


def partition(*, action="alert_manual", reason="data_contract_failure", failure_class="data_contract"):
    failed = FailedRunEvidence(
        run_id="failed-1",
        status="FAILURE",
        failure_class=failure_class,
        failure_source="dbt_artifact",
        failure_component="dbt:build",
        failure_reason="dbt_data_test_failed",
        failure_stage="dbt:build",
    )
    return PartitionIncidentEvidence(
        partition_key="2026-08-05",
        freshness_overdue=True,
        exact_partition_complete=False,
        missing_mart_asset_keys=("orders", "order_items"),
        run_ids=("failed-1",),
        failed_run_ids=("failed-1",),
        successful_run_ids=(),
        latest_failed_run=failed,
        recovery=RecoveryPolicySnapshot(
            action=action,
            reason_code=reason,
            explanation="phase6f-test",
            observed_auto_replay_attempts=0,
            active_run_ids=(),
            active_recovery_run_ids=(),
        ),
        infrastructure_healthy=True,
    )


def incident(*, evidence="RUNTIME_VERIFIED"):
    return IncidentDrilldownResult(
        status=IncidentDrilldownStatus.COMPLETE,
        evidence=evidence,
        partitions=(partition(),),
        validation="PHASE6F_TEST_INCIDENT",
    )


def human(subject="operator-42"):
    return ApprovalActor(
        subject_id=subject,
        actor_type=ApprovalActorType.HUMAN_OPERATOR,
        authenticated=True,
        identity_source="AUTHENTICATED_UPSTREAM",
    )


def response_plan(monkeypatch, inc=None):
    monkeypatch.setenv("PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING", "true")
    return GovernedIncidentResponsePlanner(ROOT).plan(inc or incident())


def workflow(monkeypatch):
    monkeypatch.setenv("PHASE6F_ALLOW_APPROVAL_WORKFLOW", "true")
    return GovernedApprovalWorkflow(ROOT)


def test_policy_makes_approval_a_non_execution_boundary():
    policy = yaml.safe_load((ROOT / "agent/contracts/approval_workflow_policy.yml").read_text())
    p = policy["principles"]
    assert p["approval_is_not_execution"] is True
    assert p["approval_does_not_override_phase3c_recovery_policy"] is True
    assert p["current_truth_must_be_revalidated_before_external_execution"] is True
    assert p["agent_cannot_self_approve"] is True
    assert p["agent_cannot_execute_approved_action"] is True
    assert p["audit_events_are_append_only_and_hash_chained"] is True
    assert p["audit_hash_chain_is_not_identity_signature"] is True
    assert p["no_dagster_write_handle"] is True
    assert policy["runtime"]["production_action_writes_enabled"] is False


def test_gate_defers_before_approval_requests_are_created(monkeypatch):
    monkeypatch.setenv("PHASE6F_ALLOW_APPROVAL_WORKFLOW", "false")
    bundle = GovernedApprovalWorkflow(ROOT).prepare(incident(), response_plan(monkeypatch), now=NOW)
    assert bundle.status is ApprovalWorkflowStatus.DEFERRED
    assert bundle.cases == ()
    assert bundle.validation == "NOT_EXECUTED"


def test_only_human_required_phase6e_steps_become_pending_cases(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    assert plan.status is IncidentResponseStatus.HUMAN_ACTION_REQUIRED
    bundle = wf.prepare(inc, plan, now=NOW)
    assert bundle.status is ApprovalWorkflowStatus.PENDING
    assert [case.request.action for case in bundle.cases] == [
        "INVESTIGATE_DATA_CONTRACT",
        "APPROVE_MANUAL_BACKFILL",
        "VERIFY_EXACT_PARTITION_COMPLETION",
    ]
    assert all(case.status is ApprovalStatus.PENDING for case in bundle.cases)
    assert all(case.request.execution_authorized_by_agent is False for case in bundle.cases)
    assert all(len(case.events) == 1 for case in bundle.cases)


def test_auto_replay_plan_creates_no_human_approval(monkeypatch):
    inc = IncidentDrilldownResult(
        status=IncidentDrilldownStatus.COMPLETE,
        evidence="RUNTIME_VERIFIED",
        partitions=(partition(action="auto_replay", reason="transient_failure_after_runtime_recovered", failure_class="transient_runtime"),),
        validation="AUTO_REPLAY",
    )
    plan = response_plan(monkeypatch, inc)
    bundle = workflow(monkeypatch).prepare(inc, plan, now=NOW)
    assert bundle.status is ApprovalWorkflowStatus.NO_APPROVAL_REQUIRED
    assert bundle.cases == ()


def test_authenticated_human_can_approve_but_agent_still_cannot_execute(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[1]
    approved = wf.approve(
        case,
        actor=human(),
        now=NOW + timedelta(minutes=5),
        reason="Validated the dbt contract fix and approved one manual backfill.",
        expected_evidence_fingerprint=case.request.evidence_fingerprint,
    )
    assert approved.status is ApprovalStatus.APPROVED
    assert approved.events[-1].actor.subject_id == "operator-42"
    check = wf.validate_for_external_execution(
        approved,
        current_incident=inc,
        current_response_plan=plan,
        now=NOW + timedelta(minutes=6),
    )
    assert check.status is ApprovalAuthorizationStatus.ELIGIBLE_FOR_EXTERNAL_EXECUTION
    assert check.eligible_for_external_execution is True
    assert check.agent_execution_allowed is False


def test_agent_or_unauthenticated_actor_cannot_self_approve(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[0]
    with pytest.raises(ApprovalTransitionError, match="Only HUMAN_OPERATOR"):
        wf.approve(
            case,
            actor=ApprovalActor("agent", ApprovalActorType.AGENT, True, "AUTHENTICATED_UPSTREAM"),
            now=NOW + timedelta(minutes=1),
            reason="self approval",
        )
    with pytest.raises(ApprovalTransitionError, match="authenticated"):
        wf.approve(
            case,
            actor=ApprovalActor("operator", ApprovalActorType.HUMAN_OPERATOR, False, "AUTHENTICATED_UPSTREAM"),
            now=NOW + timedelta(minutes=1),
            reason="not authenticated",
        )


def test_rejection_is_terminal_and_cannot_be_reapproved(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[0]
    rejected = wf.reject(case, actor=human(), now=NOW + timedelta(minutes=1), reason="Needs more evidence")
    assert rejected.status is ApprovalStatus.REJECTED
    with pytest.raises(ApprovalTransitionError, match="terminal state"):
        wf.approve(rejected, actor=human(), now=NOW + timedelta(minutes=2), reason="changed mind")


def test_pending_request_expires_and_cannot_authorize_execution(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[0]
    expired = wf.expire(case, now=NOW + timedelta(minutes=61))
    assert expired.status is ApprovalStatus.EXPIRED
    check = wf.validate_for_external_execution(
        expired,
        current_incident=inc,
        current_response_plan=plan,
        now=NOW + timedelta(minutes=62),
    )
    assert check.status is ApprovalAuthorizationStatus.EXPIRED
    assert check.eligible_for_external_execution is False


def test_evidence_change_makes_previous_approval_stale(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[1]
    approved = wf.approve(case, actor=human(), now=NOW + timedelta(minutes=1), reason="approved")

    changed_incident = replace(
        inc,
        partitions=(replace(inc.partitions[0], missing_mart_asset_keys=("orders",)),),
    )
    check = wf.validate_for_external_execution(
        approved,
        current_incident=changed_incident,
        current_response_plan=plan,
        now=NOW + timedelta(minutes=2),
    )
    assert check.status is ApprovalAuthorizationStatus.EVIDENCE_CHANGED
    assert check.eligible_for_external_execution is False


def test_action_disappearing_from_current_response_invalidates_approval(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[1]
    approved = wf.approve(case, actor=human(), now=NOW + timedelta(minutes=1), reason="approved")
    changed_part = replace(plan.partitions[0], steps=tuple(x for x in plan.partitions[0].steps if x.action.value != case.request.action))
    changed_plan = replace(plan, partitions=(changed_part,))
    check = wf.validate_for_external_execution(
        approved,
        current_incident=inc,
        current_response_plan=changed_plan,
        now=NOW + timedelta(minutes=2),
    )
    assert check.status is ApprovalAuthorizationStatus.ACTION_NO_LONGER_PRESENT


def test_audit_hash_chain_detects_mutated_event(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[0]
    approved = wf.approve(case, actor=human(), now=NOW + timedelta(minutes=1), reason="approved")
    mutated_event = replace(approved.events[-1], reason="tampered")
    mutated = replace(approved, events=(approved.events[0], mutated_event))
    with pytest.raises(ApprovalTransitionError, match="event hash mismatch"):
        wf.assert_integrity(mutated)


def test_jsonl_audit_store_requires_explicit_write_gate(monkeypatch, tmp_path):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    case = wf.prepare(inc, plan, now=NOW).cases[0]
    store = JsonlApprovalAuditStore(tmp_path / "approvals.jsonl")
    monkeypatch.setenv("PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE", "false")
    with pytest.raises(ApprovalAuditWriteRefused):
        store.append_new_case(case)
    monkeypatch.setenv("PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE", "true")
    store.append_new_case(case)
    approved = wf.approve(case, actor=human(), now=NOW + timedelta(minutes=1), reason="approved")
    store.append_event(approved.request.request_hash, approved.events[-1])
    lines = (tmp_path / "approvals.jsonl").read_text().splitlines()
    assert len(lines) == 2


def test_response_composer_preserves_approved_not_executed_boundary(monkeypatch):
    wf = workflow(monkeypatch)
    inc = incident()
    plan = response_plan(monkeypatch, inc)
    bundle = wf.prepare(inc, plan, now=NOW)
    approved = wf.approve(bundle.cases[1], actor=human(), now=NOW + timedelta(minutes=1), reason="approved")
    bundle.cases = (approved,)
    envelope = ApprovalWorkflowEvidenceComposer(ROOT).compose("审批状态呢？", "gross_sales", bundle)
    assert any(c.kind is ClaimKind.APPROVAL_STATUS for c in envelope.claims)
    assert any(c.kind is ClaimKind.APPROVAL_AUDIT for c in envelope.claims)
    assert any("APPROVED is not EXECUTED" in x for x in envelope.limitations)
    draft = render_deterministic(envelope)
    assert "status=APPROVED" in draft.answer
    assert validate_answer_draft(envelope, draft) is True


def test_workflow_source_contains_no_dagster_or_backfill_execution_handle():
    text = (ROOT / "agent/approval_workflow/workflow.py").read_text(encoding="utf-8")
    for symbol in GovernedApprovalWorkflow.FORBIDDEN_EXECUTION_SYMBOLS:
        # The symbols are listed once in the forbidden-symbol constant itself; no callable use is allowed.
        assert text.count(symbol) == 1, symbol
    assert "subprocess" not in text
    assert "os.system" not in text
