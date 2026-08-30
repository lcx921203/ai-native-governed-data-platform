from __future__ import annotations

import json
import os
from pathlib import Path

from agent.incident_drilldown import (
    FailedRunEvidence,
    IncidentDrilldownResult,
    IncidentDrilldownStatus,
    PartitionIncidentEvidence,
    RecoveryPolicySnapshot,
)
from agent.incident_response import GovernedIncidentResponsePlanner, IncidentResponseEvidenceComposer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "agent/generated/incident_response_samples.json"


def part(*, action, reason, failure_class, active=(), active_recovery=(), attempts=0, infra=True):
    return PartitionIncidentEvidence(
        partition_key="2026-08-05",
        freshness_overdue=True,
        exact_partition_complete=False,
        missing_mart_asset_keys=("orders", "order_items"),
        run_ids=("run-failed-1", *active),
        failed_run_ids=("run-failed-1",),
        successful_run_ids=(),
        latest_failed_run=FailedRunEvidence(
            run_id="run-failed-1",
            status="FAILURE",
            failure_class=failure_class,
            failure_source="dbt_artifact" if failure_class == "data_contract" else "runtime",
            failure_component="dbt:build" if failure_class == "data_contract" else "spark-thrift",
            failure_reason="dbt_data_test_failed" if failure_class == "data_contract" else "command_timeout",
            failure_stage="dbt:build" if failure_class == "data_contract" else "lakehouse/jobs/normalize_shopify_orders.py",
        ),
        recovery=RecoveryPolicySnapshot(
            action=action,
            reason_code=reason,
            explanation="Illustrative Phase 3C policy snapshot.",
            observed_auto_replay_attempts=attempts,
            active_run_ids=tuple(active),
            active_recovery_run_ids=tuple(active_recovery),
        ),
        infrastructure_healthy=infra,
    )


def incident(item):
    return IncidentDrilldownResult(
        status=IncidentDrilldownStatus.COMPLETE,
        evidence="RUNTIME_VERIFIED",
        partitions=(item,),
        validation="ILLUSTRATIVE_STATIC_SAMPLE_ONLY",
    )


os.environ["PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING"] = "true"
planner = GovernedIncidentResponsePlanner(ROOT)
composer = IncidentResponseEvidenceComposer(ROOT)

cases = {
    "data_contract_manual": incident(part(
        action="alert_manual",
        reason="data_contract_failure",
        failure_class="data_contract",
    )),
    "transient_auto_replay_delegated": incident(part(
        action="auto_replay",
        reason="transient_failure_after_runtime_recovered",
        failure_class="transient_runtime",
    )),
    "active_recovery_wait": incident(part(
        action="wait",
        reason="active_run_owns_partition",
        failure_class="transient_runtime",
        active=("recovery-run-1",),
        active_recovery=("recovery-run-1",),
        attempts=1,
    )),
}

payload = {
    "notice": "Illustrative contract samples only. RUNTIME_VERIFIED labels below describe fake test evidence and are not real production observations.",
    "cases": {},
}
for name, item in cases.items():
    plan = planner.plan(item)
    payload["cases"][name] = {
        "incident": item.to_dict(),
        "response_plan": plan.to_dict(),
        "response_envelope": composer.compose("现在应该怎么处理？", "gross_sales", plan).to_dict(),
    }

OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(OUT)
