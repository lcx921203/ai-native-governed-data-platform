from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent.approval_workflow import ApprovalActor, ApprovalActorType, GovernedApprovalWorkflow
from agent.incident_drilldown import (
    FailedRunEvidence,
    IncidentDrilldownResult,
    IncidentDrilldownStatus,
    PartitionIncidentEvidence,
    RecoveryPolicySnapshot,
)
from agent.incident_response import GovernedIncidentResponsePlanner

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "agent/generated/approval_workflow_samples.json"
NOW = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)


def make_incident():
    failed = FailedRunEvidence(
        run_id="failed-dbt-20260805",
        status="FAILURE",
        failure_class="data_contract",
        failure_source="dbt_artifact",
        failure_component="dbt:build",
        failure_reason="dbt_data_test_failed",
        failure_stage="dbt:build",
    )
    part = PartitionIncidentEvidence(
        partition_key="2026-08-05",
        freshness_overdue=True,
        exact_partition_complete=False,
        missing_mart_asset_keys=("orders", "order_items"),
        run_ids=("failed-dbt-20260805",),
        failed_run_ids=("failed-dbt-20260805",),
        successful_run_ids=(),
        latest_failed_run=failed,
        recovery=RecoveryPolicySnapshot(
            action="alert_manual",
            reason_code="data_contract_failure",
            explanation="Manual remediation is required before backfill.",
            observed_auto_replay_attempts=0,
            active_run_ids=(),
            active_recovery_run_ids=(),
        ),
        infrastructure_healthy=True,
    )
    return IncidentDrilldownResult(
        status=IncidentDrilldownStatus.COMPLETE,
        evidence="RUNTIME_VERIFIED",
        partitions=(part,),
        validation="PHASE6F_SAMPLE_RUNTIME_FIXTURE",
    )


def main():
    old6e = os.environ.get("PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING")
    old6f = os.environ.get("PHASE6F_ALLOW_APPROVAL_WORKFLOW")
    try:
        os.environ["PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING"] = "true"
        os.environ["PHASE6F_ALLOW_APPROVAL_WORKFLOW"] = "true"
        incident = make_incident()
        response_plan = GovernedIncidentResponsePlanner(ROOT).plan(incident)
        workflow = GovernedApprovalWorkflow(ROOT)
        bundle = workflow.prepare(incident, response_plan, now=NOW)
        actor = ApprovalActor(
            subject_id="demo-data-operator",
            actor_type=ApprovalActorType.HUMAN_OPERATOR,
            authenticated=True,
            identity_source="AUTHENTICATED_UPSTREAM",
        )
        approved = workflow.approve(
            bundle.cases[1],
            actor=actor,
            now=NOW + timedelta(minutes=5),
            reason="Data contract remediation validated; approve one manual backfill action.",
        )
        rejected = workflow.reject(
            bundle.cases[0],
            actor=actor,
            now=NOW + timedelta(minutes=6),
            reason="Investigation evidence is not sufficient yet.",
        )
        expired = workflow.expire(bundle.cases[2], now=NOW + timedelta(minutes=61))
        authorization = workflow.validate_for_external_execution(
            approved,
            current_incident=incident,
            current_response_plan=response_plan,
            now=NOW + timedelta(minutes=7),
        )
        changed_incident = replace(
            incident,
            partitions=(replace(incident.partitions[0], missing_mart_asset_keys=("orders",)),),
        )
        stale = workflow.validate_for_external_execution(
            approved,
            current_incident=changed_incident,
            current_response_plan=response_plan,
            now=NOW + timedelta(minutes=8),
        )
        payload = {
            "note": "Static engineering sample. Actor authentication and runtime incident facts are simulated; no production action was executed.",
            "initial_bundle": bundle.to_dict(),
            "approved_case": approved.to_dict(),
            "rejected_case": rejected.to_dict(),
            "expired_case": expired.to_dict(),
            "authorization_check_same_evidence": authorization.to_dict(),
            "authorization_check_after_evidence_change": stale.to_dict(),
        }
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(OUT)
    finally:
        if old6e is None:
            os.environ.pop("PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING", None)
        else:
            os.environ["PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING"] = old6e
        if old6f is None:
            os.environ.pop("PHASE6F_ALLOW_APPROVAL_WORKFLOW", None)
        else:
            os.environ["PHASE6F_ALLOW_APPROVAL_WORKFLOW"] = old6f


if __name__ == "__main__":
    main()
