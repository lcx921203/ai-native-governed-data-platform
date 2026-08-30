from __future__ import annotations

from pathlib import Path

import yaml

from agent.incident_drilldown import (
    FailedRunEvidence,
    IncidentDrilldownResult,
    IncidentDrilldownStatus,
    PartitionIncidentEvidence,
    RecoveryPolicySnapshot,
)
from agent.incident_response import (
    ApprovalBoundary,
    GovernedIncidentResponsePlanner,
    IncidentResponseEvidenceComposer,
    IncidentResponseStatus,
    ResponseActionKind,
    ResponseAuthority,
)
from agent.response import ClaimKind, render_deterministic, validate_answer_draft

ROOT = Path(__file__).resolve().parents[1]


def partition(
    *,
    action="alert_manual",
    reason="data_contract_failure",
    failure_class="data_contract",
    complete=False,
    active=(),
    active_recovery=(),
    infra=True,
    attempts=0,
    run_id="failed-1",
):
    failed = None if run_id is None else FailedRunEvidence(
        run_id=run_id,
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
        exact_partition_complete=complete,
        missing_mart_asset_keys=() if complete else ("orders", "order_items"),
        run_ids=tuple(x for x in (run_id, *active) if x),
        failed_run_ids=(run_id,) if run_id else (),
        successful_run_ids=(),
        latest_failed_run=failed,
        recovery=RecoveryPolicySnapshot(
            action=action,
            reason_code=reason,
            explanation="phase6e-test",
            observed_auto_replay_attempts=attempts,
            active_run_ids=tuple(active),
            active_recovery_run_ids=tuple(active_recovery),
        ),
        infrastructure_healthy=infra,
    )


def incident(*parts, status=IncidentDrilldownStatus.COMPLETE, evidence="RUNTIME_VERIFIED"):
    return IncidentDrilldownResult(
        status=status,
        evidence=evidence,
        partitions=tuple(parts),
        validation="PHASE6E_TEST_INCIDENT",
    )


def enable(monkeypatch):
    monkeypatch.setenv("PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING", "true")


def actions(plan):
    return [step.action for p in plan.partitions for step in p.steps]


def test_policy_is_advisory_only_and_preserves_phase3c_authority():
    policy = yaml.safe_load((ROOT / "agent/contracts/incident_response_policy.yml").read_text())
    p = policy["principles"]
    assert p["phase6d_runtime_verified_incident_required"] is True
    assert p["phase3c_recovery_policy_is_execution_authority"] is True
    assert p["agent_never_launches_recovery_run"] is True
    assert p["agent_never_launches_manual_backfill"] is True
    assert p["auto_replay_is_delegated_to_existing_dagster_recovery_sensor"] is True
    assert p["manual_recovery_requires_human_approval"] is True
    assert p["policy_decision_is_not_observed_execution"] is True
    assert p["arbitrary_sql"] is False
    assert p["arbitrary_shell_command"] is False
    assert policy["runtime"]["writes_enabled"] is False


def test_gate_defers_before_response_planning(monkeypatch):
    monkeypatch.setenv("PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING", "false")
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition()))
    assert plan.status is IncidentResponseStatus.DEFERRED
    assert plan.validation == "NOT_EXECUTED"
    assert plan.partitions == ()


def test_auto_replay_is_delegated_to_existing_dagster_sensor_not_agent(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="auto_replay",
        reason="transient_failure_after_runtime_recovered",
        failure_class="transient_runtime",
    )))
    assert plan.status is IncidentResponseStatus.DELEGATED
    part = plan.partitions[0]
    assert part.steps[0].action is ResponseActionKind.DELEGATE_AUTO_REPLAY
    assert part.steps[0].authority is ResponseAuthority.DAGSTER_RECOVERY_SENSOR
    assert part.steps[0].approval_boundary is ApprovalBoundary.AUTOMATION_POLICY_OWNED
    assert all(step.executable_by_agent is False for step in part.steps)
    assert ResponseActionKind.VERIFY_EXACT_PARTITION_COMPLETION in actions(plan)


def test_active_recovery_owner_blocks_duplicate_action(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="wait",
        reason="active_run_owns_partition",
        failure_class="transient_runtime",
        active=("recovery-1",),
        active_recovery=("recovery-1",),
        attempts=1,
    )))
    assert plan.status is IncidentResponseStatus.WAITING
    assert plan.partitions[0].steps[0].action is ResponseActionKind.WAIT_FOR_ACTIVE_RECOVERY
    assert ResponseActionKind.DELEGATE_AUTO_REPLAY not in actions(plan)
    assert all(step.executable_by_agent is False for step in plan.partitions[0].steps)


def test_data_contract_requires_repair_then_human_backfill_approval(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition()))
    assert plan.status is IncidentResponseStatus.HUMAN_ACTION_REQUIRED
    part = plan.partitions[0]
    assert [x.action for x in part.steps] == [
        ResponseActionKind.INVESTIGATE_DATA_CONTRACT,
        ResponseActionKind.APPROVE_MANUAL_BACKFILL,
        ResponseActionKind.VERIFY_EXACT_PARTITION_COMPLETION,
    ]
    assert part.human_approval_required is True
    assert all(x.approval_boundary is ApprovalBoundary.HUMAN_REQUIRED for x in part.steps)
    assert all(x.executable_by_agent is False for x in part.steps)


def test_deterministic_code_never_replays_before_fix(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="alert_manual",
        reason="deterministic_code_failure",
        failure_class="deterministic_code",
    )))
    assert plan.partitions[0].steps[0].action is ResponseActionKind.FIX_DETERMINISTIC_CODE
    assert ResponseActionKind.DELEGATE_AUTO_REPLAY not in actions(plan)


def test_infrastructure_unhealthy_requires_platform_restore_before_reevaluation(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="alert_and_wait",
        reason="infrastructure_unhealthy",
        failure_class="infrastructure_unavailable",
        infra=False,
    )))
    part = plan.partitions[0]
    assert part.status is IncidentResponseStatus.HUMAN_ACTION_REQUIRED
    assert [x.action for x in part.steps] == [
        ResponseActionKind.RESTORE_INFRASTRUCTURE,
        ResponseActionKind.REEVALUATE_RECOVERY_POLICY,
    ]
    assert part.steps[0].authority is ResponseAuthority.PLATFORM_OPERATOR
    assert ResponseActionKind.DELEGATE_AUTO_REPLAY not in actions(plan)


def test_unknown_failure_and_historical_no_run_remain_fail_closed(monkeypatch):
    enable(monkeypatch)
    unknown = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="alert_manual",
        reason="unknown_failure_class",
        failure_class="unknown",
    )))
    assert unknown.partitions[0].steps[0].action is ResponseActionKind.INVESTIGATE_UNKNOWN_FAILURE

    no_run = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="alert_manual",
        reason="historical_no_run_requires_manual_backfill",
        failure_class="none",
        run_id=None,
    )))
    assert no_run.partitions[0].steps[0].action is ResponseActionKind.REVIEW_HISTORICAL_NO_RUN
    assert no_run.partitions[0].steps[1].action is ResponseActionKind.APPROVE_MANUAL_BACKFILL


def test_replay_budget_exhausted_needs_human_review_not_attempt_two(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="alert_manual",
        reason="auto_replay_budget_exhausted",
        failure_class="transient_runtime",
        attempts=1,
    )))
    assert plan.partitions[0].steps[0].action is ResponseActionKind.INVESTIGATE_REPLAY_EXHAUSTION
    assert ResponseActionKind.DELEGATE_AUTO_REPLAY not in actions(plan)


def test_success_run_without_complete_partition_requires_reconciliation(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(
        action="alert_manual",
        reason="successful_run_without_complete_partition",
        failure_class="none",
    )))
    assert plan.partitions[0].steps[0].action is ResponseActionKind.VALIDATE_SUCCESS_WITH_INCOMPLETE_PARTITION


def test_non_runtime_incident_evidence_cannot_generate_action_plan(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition(), evidence="STATIC_CONTRACT"))
    assert plan.status is IncidentResponseStatus.DEFERRED
    assert plan.validation == "INCIDENT_EVIDENCE_NOT_RUNTIME_VERIFIED"
    assert plan.partitions == ()


def test_response_composer_exposes_plan_and_authority_without_claiming_execution(monkeypatch):
    enable(monkeypatch)
    plan = GovernedIncidentResponsePlanner(ROOT).plan(incident(partition()))
    envelope = IncidentResponseEvidenceComposer(ROOT).compose("现在怎么办？", "gross_sales", plan)
    assert any(c.kind is ClaimKind.INCIDENT_RESPONSE_PLAN for c in envelope.claims)
    authority = next(c for c in envelope.claims if c.kind is ClaimKind.ACTION_AUTHORITY)
    assert authority.runtime_observed is False
    assert "agent_execution_allowed=false" in authority.text
    assert any("no production recovery/backfill write authority" in x for x in envelope.limitations)
    draft = render_deterministic(envelope)
    assert validate_answer_draft(envelope, draft) is True
    assert "APPROVE_MANUAL_BACKFILL" in draft.answer


def test_every_governed_response_step_is_non_executable_by_agent(monkeypatch):
    enable(monkeypatch)
    examples = [
        partition(action="auto_replay", reason="transient_failure_after_runtime_recovered", failure_class="transient_runtime"),
        partition(action="wait", reason="active_run_owns_partition", active=("run-1",)),
        partition(action="alert_and_wait", reason="infrastructure_unhealthy", infra=False),
        partition(action="alert_manual", reason="data_contract_failure"),
    ]
    planner = GovernedIncidentResponsePlanner(ROOT)
    for item in examples:
        plan = planner.plan(incident(item))
        assert all(step.executable_by_agent is False for part in plan.partitions for step in part.steps)


def test_incident_response_planner_has_no_dagster_write_surface():
    source=(ROOT/'agent/incident_response/planner.py').read_text(encoding='utf-8')
    forbidden=(
        'DagsterInstance',
        'RunRequest(',
        '.submit_run(',
        '.create_run(',
        'execute_job(',
        'execute_in_process(',
    )
    for token in forbidden:
        assert token not in source, token
    assert 'writes_enabled' in source and 'advisory-only' in source
