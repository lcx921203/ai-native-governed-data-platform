from __future__ import annotations

from pathlib import Path

from agent.driver_attribution import DriverAttributionStatus
from agent.response import AnswerStatus, Claim, ClaimKind, ResponseEnvelope

from .contracts import DiagnosticResult, DiagnosticStatus


class DiagnosticEvidenceComposer:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()

    def compose(self, diagnostic: DiagnosticResult) -> ResponseEnvelope:
        claims: list[Claim] = []
        limitations: list[str] = []
        cid = 1

        def add(kind, text, *, evidence="STATIC_CONTRACT", locations=(), runtime=False):
            nonlocal cid
            claims.append(
                Claim(
                    id=f"C{cid:02d}",
                    kind=kind,
                    text=text,
                    evidence=evidence,
                    source_locations=tuple(locations),
                    runtime_observed=runtime,
                )
            )
            cid += 1

        anomaly = diagnostic.anomaly
        if anomaly and anomaly.current_value is not None and anomaly.baseline_value is not None:
            add(
                ClaimKind.ANOMALY_OBSERVATION,
                (
                    f"{anomaly.plan.metric} anomaly observation: current={anomaly.current_value}; "
                    f"median_baseline={anomaly.baseline_value}; absolute_change={anomaly.absolute_change}; "
                    f"relative_change_percent={anomaly.relative_change_percent}; "
                    f"state={anomaly.anomaly_state.value}; direction={anomaly.direction.value}."
                ),
                evidence=anomaly.evidence,
                locations=(
                    "agent/contracts/anomaly_detection_policy.yml",
                    "agent/anomaly_analysis/detector.py",
                ),
                runtime=anomaly.evidence == "RUNTIME_VERIFIED",
            )

        health = diagnostic.operational_health
        if health and health.evidence == "RUNTIME_VERIFIED":
            add(
                ClaimKind.OPERATIONAL_HEALTH,
                f"Operational health: {health.state.value}. {health.details}".strip(),
                evidence="RUNTIME_VERIFIED",
                locations=(
                    "agent/diagnostic/operational_health.py",
                    "orchestration/dagster/commerce_dagster/recovery_state.py",
                ),
                runtime=True,
            )
        elif health:
            limitations.append(
                "Operational-health runtime evidence is unavailable or not verified: " + (health.details or health.evidence)
            )

        if anomaly and anomaly.cause_class.value != "UNRESOLVED":
            evidence = "RUNTIME_VERIFIED" if anomaly.evidence == "RUNTIME_VERIFIED" else anomaly.evidence
            add(
                ClaimKind.DIAGNOSTIC_CLASSIFICATION,
                (
                    f"Diagnostic classification: {anomaly.cause_class.value}. "
                    "This is a governed evidence classification, not a confirmed causal root cause."
                ),
                evidence=evidence,
                locations=("agent/anomaly_analysis/detector.py",),
                runtime=evidence == "RUNTIME_VERIFIED",
            )

        incident = diagnostic.incident
        if incident:
            if incident.evidence == "RUNTIME_VERIFIED":
                incomplete_incident_partitions = [item for item in incident.partitions if not item.exact_partition_complete]
                selected_incident_partitions = incomplete_incident_partitions[:3]
                if len(incomplete_incident_partitions) > len(selected_incident_partitions):
                    limitations.append(
                        f"Incident answer is capped at {len(selected_incident_partitions)} incomplete partitions; "
                        f"{len(incomplete_incident_partitions) - len(selected_incident_partitions)} additional incomplete partition(s) remain in structured drilldown output."
                    )
                for item in selected_incident_partitions:
                    add(
                        ClaimKind.INCIDENT_EVIDENCE,
                        (
                            f"Partition {item.partition_key} is incomplete; "
                            f"missing_marts={','.join(item.missing_mart_asset_keys) or 'none'}; "
                            f"freshness_overdue={str(item.freshness_overdue).lower()}."
                        ),
                        evidence="RUNTIME_VERIFIED",
                        locations=(
                            "orchestration/dagster/commerce_dagster/recovery_state.py",
                            "agent/incident_drilldown/provider.py",
                        ),
                        runtime=True,
                    )
                    if item.latest_failed_run:
                        run = item.latest_failed_run
                        add(
                            ClaimKind.INCIDENT_EVIDENCE,
                            (
                                f"Latest structured failed run: run_id={run.run_id}; status={run.status}; "
                                f"failure_class={run.failure_class}; component={run.failure_component or 'unknown'}; "
                                f"reason={run.failure_reason or 'unknown'}; stage={run.failure_stage or 'unknown'}."
                            ),
                            evidence="RUNTIME_VERIFIED",
                            locations=(
                                "orchestration/dagster/commerce_dagster/failure_classification.py",
                                "agent/incident_drilldown/provider.py",
                            ),
                            runtime=True,
                        )
                    else:
                        limitations.append(
                            f"Partition {item.partition_key} is incomplete but has no structured failed-run cause evidence."
                        )
                    add(
                        ClaimKind.RECOVERY_STATUS,
                        (
                            f"Recovery for partition {item.partition_key}: "
                            f"observed_auto_replay_attempts={item.recovery.observed_auto_replay_attempts}; "
                            f"active_recovery_runs={','.join(item.recovery.active_recovery_run_ids) or 'none'}; "
                            f"policy_action_if_evaluated_now={item.recovery.action}; "
                            f"policy_reason={item.recovery.reason_code}."
                        ),
                        evidence="RUNTIME_VERIFIED",
                        locations=(
                            "orchestration/dagster/commerce_dagster/recovery_policy.py",
                            "orchestration/dagster/commerce_dagster/recovery_state.py",
                        ),
                        runtime=True,
                    )
            else:
                limitations.extend(incident.warnings or [
                    "Operational incident drilldown runtime evidence is unavailable."
                ])

        incident_response = diagnostic.incident_response
        if incident_response:
            if incident_response.evidence == "RUNTIME_VERIFIED":
                for part in incident_response.partitions[:3]:
                    actions = ",".join(step.action.value for step in part.steps) or "none"
                    add(
                        ClaimKind.INCIDENT_RESPONSE_PLAN,
                        (
                            f"Response plan for partition {part.partition_key}: status={part.status.value}; "
                            f"phase3c_policy_action={part.policy_action}; policy_reason={part.policy_reason}; actions={actions}."
                        ),
                        evidence="RUNTIME_VERIFIED",
                        locations=(
                            "agent/contracts/incident_response_policy.yml",
                            "agent/incident_response/planner.py",
                            "orchestration/dagster/commerce_dagster/recovery_policy.py",
                        ),
                        runtime=False,
                    )
                add(
                    ClaimKind.ACTION_AUTHORITY,
                    "Incident response is advisory only: Agent production execution authority=false; automatic replay belongs to the existing Dagster Recovery Sensor; manual remediation/backfill requires human approval.",
                    evidence="STATIC_CONTRACT",
                    locations=("agent/contracts/incident_response_policy.yml",),
                    runtime=False,
                )
            else:
                limitations.extend(incident_response.warnings or [
                    "Incident-response planning is unavailable without runtime-verified incident evidence."
                ])

        attribution = diagnostic.attribution
        if attribution:
            for lens in attribution.lenses:
                if lens.status is not DriverAttributionStatus.COMPLETE or lens.strongest_driver is None:
                    continue
                row = lens.strongest_driver
                parts = [
                    f"{lens.dimension} strongest anomaly-direction driver: {row.dimension_value}",
                    f"absolute_change={row.absolute_change}",
                ]
                if row.growth_rate_percent is not None:
                    parts.append(f"growth_rate_percent={row.growth_rate_percent}")
                if row.contribution_percent is not None:
                    parts.append(f"contribution_percent={row.contribution_percent}")
                add(
                    ClaimKind.DRIVER_ATTRIBUTION,
                    "; ".join(parts) + ".",
                    evidence=lens.evidence,
                    locations=(
                        "agent/contracts/driver_attribution_policy.yml",
                        "agent/driver_attribution/attribution.py",
                    ),
                    runtime=lens.evidence == "RUNTIME_VERIFIED",
                )
            if any(lens.status is not DriverAttributionStatus.COMPLETE for lens in attribution.lenses):
                limitations.append("One or more driver lenses did not complete; verified lenses are preserved without pretending full attribution coverage.")
            limitations.append(
                "Region / Brand / Category are overlapping analytical lenses; contribution percentages must not be added across lenses."
            )

        for warning in diagnostic.warnings:
            if warning not in limitations:
                limitations.append(warning)

        if diagnostic.status is DiagnosticStatus.DATA_PIPELINE_SUSPECTED:
            limitations.append(
                "Business-driver attribution is intentionally stopped while exact-partition operational health is unhealthy."
            )
        elif diagnostic.status is DiagnosticStatus.UNRESOLVED:
            limitations.append(
                "The metric signal may be observed, but business-vs-pipeline cause cannot be classified without verified healthy operational evidence."
            )
        elif diagnostic.status is DiagnosticStatus.DEFERRED:
            limitations.append(
                "Real diagnostic runtime evidence is unavailable; no anomaly, operational-health fact, or business driver may be inferred from static contracts."
            )

        limitations = list(dict.fromkeys(limitations))
        for limitation in limitations:
            add(ClaimKind.LIMITATION, limitation, evidence="DEFERRED")

        return ResponseEnvelope(
            question=diagnostic.plan.question,
            intent="DIAGNOSTIC_QUERY",
            status=self._answer_status(diagnostic.status),
            subject={"kind": "metric", "id": diagnostic.plan.metric},
            claims=claims,
            limitations=limitations,
            sources=[],
            tool_trace=[item.to_dict() for item in diagnostic.trace],
            evidence_levels=sorted({claim.evidence for claim in claims}),
        )

    @staticmethod
    def _answer_status(status: DiagnosticStatus) -> AnswerStatus:
        return {
            DiagnosticStatus.NORMAL: AnswerStatus.ANSWERED,
            DiagnosticStatus.BUSINESS_DRIVERS_IDENTIFIED: AnswerStatus.ANSWERED,
            DiagnosticStatus.DATA_PIPELINE_SUSPECTED: AnswerStatus.PARTIAL,
            DiagnosticStatus.UNRESOLVED: AnswerStatus.PARTIAL,
            DiagnosticStatus.PARTIAL: AnswerStatus.PARTIAL,
            DiagnosticStatus.DEFERRED: AnswerStatus.DEFERRED,
            DiagnosticStatus.BLOCKED: AnswerStatus.BLOCKED,
            DiagnosticStatus.ERROR: AnswerStatus.ERROR,
            DiagnosticStatus.READY: AnswerStatus.PARTIAL,
        }[status]
