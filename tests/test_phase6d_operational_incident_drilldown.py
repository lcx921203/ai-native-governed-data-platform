from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.incident_drilldown import (
    FailedRunEvidence,
    GovernedOperationalIncidentDrilldown,
    IncidentDrilldownResult,
    IncidentDrilldownStatus,
    IncidentEvidenceComposer,
    PartitionIncidentEvidence,
    RecoveryPolicySnapshot,
)
from agent.response import ClaimKind, render_deterministic, validate_answer_draft
from agent.semantic_query import SemanticQuerySpec

ROOT = Path(__file__).resolve().parents[1]


def spec(start="2026-08-05T00:00:00Z", end="2026-08-05T23:59:59Z"):
    return SemanticQuerySpec(
        metric="gross_sales",
        start_time=start,
        end_time=end,
        group_by=(),
        filters=(),
        limit=20,
    )


def partition(
    *,
    failure_class="data_contract",
    action="alert_manual",
    reason="data_contract_failure",
    run_id="run-failed-1",
    active=(),
    active_recovery=(),
    attempts=0,
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
        exact_partition_complete=False,
        missing_mart_asset_keys=("orders", "order_items"),
        run_ids=tuple(x for x in (run_id, *active) if x),
        failed_run_ids=(run_id,) if run_id else (),
        successful_run_ids=(),
        latest_failed_run=failed,
        recovery=RecoveryPolicySnapshot(
            action=action,
            reason_code=reason,
            explanation="policy-test",
            observed_auto_replay_attempts=attempts,
            active_run_ids=tuple(active),
            active_recovery_run_ids=tuple(active_recovery),
        ),
        infrastructure_healthy=True,
    )


class FixedProvider:
    def __init__(self, result):
        self.result=result
        self.calls=[]

    def inspect(self, query_spec):
        self.calls.append(query_spec)
        return self.result


def test_policy_reuses_phase3c_truth_and_never_parses_free_text_cause():
    policy=yaml.safe_load((ROOT/'agent/contracts/operational_incident_policy.yml').read_text())
    p=policy['principles']
    assert p['exact_partition_current_truth_precedes_run_status'] is True
    assert p['structured_failure_tags_only'] is True
    assert p['free_text_log_cause_inference'] is False
    assert p['phase3c_failure_classification_is_source_of_truth'] is True
    assert p['phase3c_recovery_policy_is_source_of_truth'] is True
    assert p['recovery_policy_decision_is_not_observed_recovery_execution'] is True
    assert p['no_failed_run_does_not_prove_missed_schedule'] is True


def test_structured_incident_result_enters_claim_ledger_without_overclaiming_recovery_execution():
    result=IncidentDrilldownResult(
        status=IncidentDrilldownStatus.COMPLETE,
        evidence='RUNTIME_VERIFIED',
        partitions=(partition(action='alert_manual', reason='data_contract_failure'),),
        validation='STRUCTURED_INCIDENT_EVIDENCE_COLLECTED',
    )
    envelope=IncidentEvidenceComposer(ROOT).compose('why?', 'gross_sales', result)
    kinds=[c.kind for c in envelope.claims]
    assert kinds.count(ClaimKind.INCIDENT_EVIDENCE)==2
    assert kinds.count(ClaimKind.RECOVERY_STATUS)==1
    recovery=next(c.text for c in envelope.claims if c.kind is ClaimKind.RECOVERY_STATUS)
    assert 'policy_action_if_evaluated_now=alert_manual' in recovery
    assert 'observed_auto_replay_attempts=0' in recovery
    assert 'recovery executed' not in recovery.lower()
    draft=render_deterministic(envelope)
    assert validate_answer_draft(envelope,draft) is True


def test_incomplete_partition_without_failed_run_keeps_cause_unknown():
    result=IncidentDrilldownResult(
        status=IncidentDrilldownStatus.COMPLETE,
        evidence='RUNTIME_VERIFIED',
        partitions=(partition(run_id=None, action='auto_replay', reason='missed_schedule_or_no_run'),),
        warnings=['No structured failed run exists.'],
        validation='STRUCTURED_INCIDENT_EVIDENCE_COLLECTED',
    )
    envelope=IncidentEvidenceComposer(ROOT).compose('why?', 'gross_sales', result)
    assert not any('failure_class=' in c.text for c in envelope.claims if c.kind is ClaimKind.INCIDENT_EVIDENCE)
    assert any('failure cause remains unknown' in item for item in envelope.limitations)
    assert any('policy_action_if_evaluated_now=auto_replay' in c.text for c in envelope.claims)


def test_active_recovery_owner_is_reported_separately_from_replay_attempt_count():
    result=IncidentDrilldownResult(
        status=IncidentDrilldownStatus.COMPLETE,
        evidence='RUNTIME_VERIFIED',
        partitions=(partition(
            action='wait',
            reason='active_run_owns_partition',
            active=('recovery-run-1',),
            active_recovery=('recovery-run-1',),
            attempts=1,
        ),),
        validation='STRUCTURED_INCIDENT_EVIDENCE_COLLECTED',
    )
    envelope=IncidentEvidenceComposer(ROOT).compose('why?', 'gross_sales', result)
    text=next(c.text for c in envelope.claims if c.kind is ClaimKind.RECOVERY_STATUS)
    assert 'active_recovery_runs=recovery-run-1' in text
    assert 'observed_auto_replay_attempts=1' in text
    assert 'policy_reason=active_run_owns_partition' in text


def test_unknown_failure_class_is_allowed_but_unrecognized_class_is_fail_closed():
    ok=GovernedOperationalIncidentDrilldown(
        ROOT,
        runtime_provider=FixedProvider(IncidentDrilldownResult(
            status=IncidentDrilldownStatus.COMPLETE,
            evidence='RUNTIME_VERIFIED',
            partitions=(partition(failure_class='unknown'),),
        )),
    ).execute(spec())
    assert ok.status is IncidentDrilldownStatus.COMPLETE

    blocked=GovernedOperationalIncidentDrilldown(
        ROOT,
        runtime_provider=FixedProvider(IncidentDrilldownResult(
            status=IncidentDrilldownStatus.COMPLETE,
            evidence='RUNTIME_VERIFIED',
            partitions=(partition(failure_class='magic_retryable'),),
        )),
    ).execute(spec())
    assert blocked.status is IncidentDrilldownStatus.BLOCKED
    assert blocked.validation=='UNRECOGNIZED_FAILURE_CLASS'


def test_drilldown_limits_partition_breadth_before_runtime_provider_call():
    provider=FixedProvider(IncidentDrilldownResult(status=IncidentDrilldownStatus.COMPLETE,evidence='RUNTIME_VERIFIED'))
    drilldown=GovernedOperationalIncidentDrilldown(ROOT,runtime_provider=provider)
    result=drilldown.execute(spec('2026-08-01T00:00:00Z','2026-08-10T23:59:59Z'))
    assert result.status is IncidentDrilldownStatus.BLOCKED
    assert result.validation=='INCIDENT_PARTITION_LIMIT_EXCEEDED'
    assert provider.calls==[]


def test_phase3c_structured_failure_stage_tag_preserves_execution_location_without_changing_failure_class():
    classification=(ROOT/'orchestration/dagster/commerce_dagster/failure_classification.py').read_text()
    spark=(ROOT/'orchestration/dagster/commerce_dagster/resources.py').read_text()
    dbt=(ROOT/'orchestration/dagster/commerce_dagster/dbt_failure_adapter.py').read_text()
    assert 'FAILURE_STAGE_TAG = "commerce/failure_stage"' in classification
    assert 'stage=project_relative_script' in spark
    assert 'stage=component' in dbt
    assert 'component=self.service' in spark  # existing Phase 3C component contract remains intact
