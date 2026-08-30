"""Phase 6E 的只读 Incident Response Planner。

它把 Phase 6D incident evidence 与 Chapter 04 Recovery Policy 转成建议步骤，不执行 Dagster recovery/backfill。
工程边界：AUTO_REPLAY 仍由既有 Recovery Sensor 权威处理，HUMAN_REQUIRED 只产生审批需求。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from agent.incident_drilldown import IncidentDrilldownResult, IncidentDrilldownStatus, PartitionIncidentEvidence

from .contracts import (
    ApprovalBoundary,
    IncidentResponsePlan,
    IncidentResponseStatus,
    IncidentResponseStep,
    PartitionResponsePlan,
    ResponseActionKind,
    ResponseAuthority,
)


class GovernedIncidentResponsePlanner:
    """基于结构化 incident + recovery policy 生成 advisory response plan。
    
    输出步骤包含 action、authority、approval boundary；Planner 没有执行 handle。
    """

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/incident_response_policy.yml").read_text(encoding="utf-8")
        )
        if self.policy.get("runtime", {}).get("writes_enabled") is not False:
            raise ValueError("Phase 6E policy must keep writes_enabled=false; this planner is advisory-only")

    def plan(self, incident: IncidentDrilldownResult) -> IncidentResponsePlan:
        """为所有受影响分区生成 response plan；输入证据不足或状态冲突时 Fail Closed。"""
        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            return IncidentResponsePlan(
                status=IncidentResponseStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                warnings=[
                    f"Incident-response planning is disabled; set {gate}=true only in the intended runtime environment."
                ],
                validation="NOT_EXECUTED",
            )

        if incident.evidence != "RUNTIME_VERIFIED":
            return IncidentResponsePlan(
                status=IncidentResponseStatus.DEFERRED,
                evidence=incident.evidence,
                warnings=list(dict.fromkeys([
                    *incident.warnings,
                    "Incident-response planning requires RUNTIME_VERIFIED Phase 6D structured incident evidence.",
                ])),
                validation="INCIDENT_EVIDENCE_NOT_RUNTIME_VERIFIED",
            )

        if incident.status is IncidentDrilldownStatus.NO_INCIDENT:
            return IncidentResponsePlan(
                status=IncidentResponseStatus.NO_ACTION,
                evidence="RUNTIME_VERIFIED",
                validation="NO_INCOMPLETE_PARTITION_NO_RESPONSE_ACTION",
            )

        if incident.status is not IncidentDrilldownStatus.COMPLETE:
            mapped = {
                IncidentDrilldownStatus.DEFERRED: IncidentResponseStatus.DEFERRED,
                IncidentDrilldownStatus.BLOCKED: IncidentResponseStatus.BLOCKED,
                IncidentDrilldownStatus.ERROR: IncidentResponseStatus.ERROR,
                IncidentDrilldownStatus.PARTIAL: IncidentResponseStatus.PARTIAL,
                IncidentDrilldownStatus.NO_INCIDENT: IncidentResponseStatus.NO_ACTION,
                IncidentDrilldownStatus.COMPLETE: IncidentResponseStatus.PARTIAL,
            }[incident.status]
            return IncidentResponsePlan(
                status=mapped,
                evidence=incident.evidence,
                warnings=list(incident.warnings),
                validation="INCIDENT_DRILLDOWN_NOT_COMPLETE",
            )

        incomplete = [item for item in incident.partitions if not item.exact_partition_complete]
        if not incomplete:
            return IncidentResponsePlan(
                status=IncidentResponseStatus.NO_ACTION,
                evidence="RUNTIME_VERIFIED",
                validation="NO_INCOMPLETE_PARTITION_NO_RESPONSE_ACTION",
            )

        max_partitions = int(self.policy["limits"]["max_partitions_per_plan"])
        if len(incomplete) > max_partitions:
            return IncidentResponsePlan(
                status=IncidentResponseStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                warnings=[f"Incident response supports at most {max_partitions} incomplete partitions per plan."],
                validation="INCIDENT_RESPONSE_PARTITION_LIMIT_EXCEEDED",
            )

        plans: list[PartitionResponsePlan] = []
        warnings: list[str] = []
        for item in incomplete:
            part = self._plan_partition(item)
            plans.append(part)
            if part.status is IncidentResponseStatus.BLOCKED:
                warnings.append(
                    f"Partition {item.partition_key} has a fail-closed recovery state that cannot be translated into an advisory response plan."
                )

        statuses = {item.status for item in plans}
        if IncidentResponseStatus.BLOCKED in statuses:
            overall = IncidentResponseStatus.BLOCKED
        elif IncidentResponseStatus.HUMAN_ACTION_REQUIRED in statuses:
            overall = IncidentResponseStatus.HUMAN_ACTION_REQUIRED if len(statuses) == 1 else IncidentResponseStatus.PARTIAL
        elif IncidentResponseStatus.DELEGATED in statuses:
            overall = IncidentResponseStatus.DELEGATED if statuses == {IncidentResponseStatus.DELEGATED} else IncidentResponseStatus.PARTIAL
        elif IncidentResponseStatus.WAITING in statuses:
            overall = IncidentResponseStatus.WAITING if statuses == {IncidentResponseStatus.WAITING} else IncidentResponseStatus.PARTIAL
        elif statuses == {IncidentResponseStatus.NO_ACTION}:
            overall = IncidentResponseStatus.NO_ACTION
        else:
            overall = IncidentResponseStatus.PARTIAL

        return IncidentResponsePlan(
            status=overall,
            evidence="RUNTIME_VERIFIED",
            partitions=tuple(plans),
            warnings=list(dict.fromkeys(warnings)),
            validation="ADVISORY_RESPONSE_PLAN_BUILT_NO_AGENT_EXECUTION_AUTHORITY",
        )

    def _plan_partition(self, item: PartitionIncidentEvidence) -> PartitionResponsePlan:
        """针对单个 partition 映射 policy action 为 AUTO / HUMAN_REQUIRED / BLOCKED 的建议步骤。"""
        action = item.recovery.action
        reason = item.recovery.reason_code

        def step(seq, kind, authority, boundary, rationale):
            """处理 step 对应的受治理工程步骤。
            
            输入输出沿用当前模块契约；不得绕过既有 Runtime gate、证据等级或生产写入边界。
            """
            return IncidentResponseStep(
                sequence=seq,
                action=kind,
                authority=authority,
                approval_boundary=boundary,
                rationale=rationale,
                executable_by_agent=False,
            )

        if item.exact_partition_complete:
            return PartitionResponsePlan(
                partition_key=item.partition_key,
                status=IncidentResponseStatus.NO_ACTION,
                policy_action=action,
                policy_reason=reason,
                steps=(step(1, ResponseActionKind.CLOSE_INCIDENT, ResponseAuthority.NONE, ApprovalBoundary.NONE, "Exact partition is already complete."),),
            )

        if action == "wait":
            if item.recovery.active_recovery_run_ids:
                steps = (
                    step(1, ResponseActionKind.WAIT_FOR_ACTIVE_RECOVERY, ResponseAuthority.DAGSTER_RECOVERY_SENSOR, ApprovalBoundary.AUTOMATION_POLICY_OWNED, "An active recovery run already owns this partition; do not create a duplicate replay."),
                    step(2, ResponseActionKind.VERIFY_EXACT_PARTITION_COMPLETION, ResponseAuthority.DAGSTER_RECOVERY_SENSOR, ApprovalBoundary.AUTOMATION_POLICY_OWNED, "Re-evaluate exact-partition completeness after the existing recovery run finishes."),
                )
            elif item.recovery.active_run_ids:
                steps = (
                    step(1, ResponseActionKind.WAIT_FOR_ACTIVE_RUN, ResponseAuthority.DAGSTER_RUN_OWNER, ApprovalBoundary.AUTOMATION_POLICY_OWNED, "An active Dagster run owns this partition; wait instead of launching another run."),
                    step(2, ResponseActionKind.VERIFY_EXACT_PARTITION_COMPLETION, ResponseAuthority.DAGSTER_RUN_OWNER, ApprovalBoundary.AUTOMATION_POLICY_OWNED, "Verify exact-partition completeness after the active run finishes."),
                )
            elif reason == "within_freshness_budget":
                steps = (
                    step(1, ResponseActionKind.WAIT_FOR_FRESHNESS_DEADLINE, ResponseAuthority.NONE, ApprovalBoundary.NONE, "The consumer freshness deadline has not been breached; intervention is not yet authorized."),
                )
            else:
                return self._blocked(item, "WAIT policy has no active owner and is not explained by the freshness budget.")
            return PartitionResponsePlan(item.partition_key, IncidentResponseStatus.WAITING, action, reason, steps)

        if action == "auto_replay":
            steps = (
                step(1, ResponseActionKind.DELEGATE_AUTO_REPLAY, ResponseAuthority.DAGSTER_RECOVERY_SENSOR, ApprovalBoundary.AUTOMATION_POLICY_OWNED, "Phase 3C policy permits one bounded replay; the existing Dagster recovery sensor owns execution, not the Agent."),
                step(2, ResponseActionKind.VERIFY_EXACT_PARTITION_COMPLETION, ResponseAuthority.DAGSTER_RECOVERY_SENSOR, ApprovalBoundary.AUTOMATION_POLICY_OWNED, "After replay, require exact consumer partition completion before closing the incident."),
            )
            return PartitionResponsePlan(item.partition_key, IncidentResponseStatus.DELEGATED, action, reason, steps)

        if action == "alert_and_wait":
            steps = (
                step(1, ResponseActionKind.RESTORE_INFRASTRUCTURE, ResponseAuthority.PLATFORM_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Current infrastructure is unhealthy; restore runtime dependencies before any replay is considered."),
                step(2, ResponseActionKind.REEVALUATE_RECOVERY_POLICY, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "After infrastructure health is restored, re-read current partition truth and re-evaluate Phase 3C recovery policy."),
            )
            return PartitionResponsePlan(item.partition_key, IncidentResponseStatus.HUMAN_ACTION_REQUIRED, action, reason, steps)

        if action != "alert_manual":
            return self._blocked(item, f"Unrecognized Phase 3C recovery action: {action}")

        latest = item.latest_failed_run
        failure_class = latest.failure_class if latest else "unknown"
        steps: list[IncidentResponseStep] = []

        if reason == "data_contract_failure" or failure_class == "data_contract":
            steps.append(step(1, ResponseActionKind.INVESTIGATE_DATA_CONTRACT, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Inspect the structured dbt/data-contract failure, correct or quarantine the invalid data, and re-run validation before backfill."))
        elif reason == "deterministic_code_failure" or failure_class == "deterministic_code":
            steps.append(step(1, ResponseActionKind.FIX_DETERMINISTIC_CODE, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Fix and validate the deterministic code defect before replaying the same partition."))
        elif reason == "auto_replay_budget_exhausted":
            steps.append(step(1, ResponseActionKind.INVESTIGATE_REPLAY_EXHAUSTION, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Automatic replay budget is exhausted; inspect repeated failures before any additional execution."))
        elif reason == "successful_run_without_complete_partition":
            steps.append(step(1, ResponseActionKind.VALIDATE_SUCCESS_WITH_INCOMPLETE_PARTITION, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "A SUCCESS run exists but exact consumer partition remains incomplete; reconcile asset materialization truth before backfill."))
        elif reason == "historical_no_run_requires_manual_backfill":
            steps.append(step(1, ResponseActionKind.REVIEW_HISTORICAL_NO_RUN, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "No historical run does not prove a missed schedule; confirm the historical gap really requires backfill."))
        elif reason == "unknown_failure_class" or failure_class in {"unknown", "none"}:
            steps.append(step(1, ResponseActionKind.INVESTIGATE_UNKNOWN_FAILURE, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Failure is not proven replay-safe; establish a structured cause before any replay/backfill."))
        else:
            steps.append(step(1, ResponseActionKind.MANUAL_INCIDENT_REVIEW, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Phase 3C requires manual handling; inspect structured incident evidence before authorizing execution."))

        steps.append(step(len(steps) + 1, ResponseActionKind.APPROVE_MANUAL_BACKFILL, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Only after remediation/validation may a human explicitly authorize manual backfill; the Agent does not launch it."))
        steps.append(step(len(steps) + 1, ResponseActionKind.VERIFY_EXACT_PARTITION_COMPLETION, ResponseAuthority.HUMAN_DATA_OPERATOR, ApprovalBoundary.HUMAN_REQUIRED, "Close the incident only after all required consumer marts are complete for the exact partition."))
        return PartitionResponsePlan(item.partition_key, IncidentResponseStatus.HUMAN_ACTION_REQUIRED, action, reason, tuple(steps))

    @staticmethod
    def _blocked(item: PartitionIncidentEvidence, rationale: str) -> PartitionResponsePlan:
        """构造明确的 BLOCKED response plan，并保留阻断原因。"""
        return PartitionResponsePlan(
            partition_key=item.partition_key,
            status=IncidentResponseStatus.BLOCKED,
            policy_action=item.recovery.action,
            policy_reason=item.recovery.reason_code,
            steps=(
                IncidentResponseStep(
                    sequence=1,
                    action=ResponseActionKind.MANUAL_INCIDENT_REVIEW,
                    authority=ResponseAuthority.HUMAN_DATA_OPERATOR,
                    approval_boundary=ApprovalBoundary.HUMAN_REQUIRED,
                    rationale=rationale,
                    executable_by_agent=False,
                ),
            ),
        )
