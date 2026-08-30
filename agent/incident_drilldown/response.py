from __future__ import annotations

from pathlib import Path

from agent.response import AnswerStatus, Claim, ClaimKind, ResponseEnvelope

from .contracts import IncidentDrilldownResult, IncidentDrilldownStatus


class IncidentEvidenceComposer:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()

    def compose(self, question: str, metric: str | None, result: IncidentDrilldownResult) -> ResponseEnvelope:
        claims: list[Claim] = []
        limitations: list[str] = []
        cid = 1

        def add(kind, text, *, runtime=True, evidence="RUNTIME_VERIFIED", locations=()):
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

        if result.evidence == "RUNTIME_VERIFIED":
            for item in result.partitions:
                if item.exact_partition_complete:
                    continue
                add(
                    ClaimKind.INCIDENT_EVIDENCE,
                    (
                        f"Partition {item.partition_key} is incomplete; "
                        f"missing_marts={','.join(item.missing_mart_asset_keys) or 'none'}; "
                        f"freshness_overdue={str(item.freshness_overdue).lower()}."
                    ),
                    locations=(
                        "orchestration/dagster/commerce_dagster/recovery_state.py",
                        "agent/incident_drilldown/provider.py",
                    ),
                )
                if item.latest_failed_run:
                    run = item.latest_failed_run
                    add(
                        ClaimKind.INCIDENT_EVIDENCE,
                        (
                            f"Latest structured failed run for partition {item.partition_key}: "
                            f"run_id={run.run_id}; status={run.status}; failure_class={run.failure_class}; "
                            f"failure_component={run.failure_component or 'unknown'}; "
                            f"failure_reason={run.failure_reason or 'unknown'}; failure_stage={run.failure_stage or 'unknown'}."
                        ),
                        locations=(
                            "orchestration/dagster/commerce_dagster/failure_classification.py",
                            "agent/incident_drilldown/provider.py",
                        ),
                    )
                else:
                    limitations.append(
                        f"Partition {item.partition_key} has no structured latest-failed-run evidence; failure cause remains unknown."
                    )
                add(
                    ClaimKind.RECOVERY_STATUS,
                    (
                        f"Recovery state for partition {item.partition_key}: "
                        f"observed_auto_replay_attempts={item.recovery.observed_auto_replay_attempts}; "
                        f"active_runs={','.join(item.recovery.active_run_ids) or 'none'}; "
                        f"active_recovery_runs={','.join(item.recovery.active_recovery_run_ids) or 'none'}; "
                        f"policy_action_if_evaluated_now={item.recovery.action}; "
                        f"policy_reason={item.recovery.reason_code}."
                    ),
                    locations=(
                        "orchestration/dagster/commerce_dagster/recovery_policy.py",
                        "orchestration/dagster/commerce_dagster/recovery_state.py",
                    ),
                )
        else:
            limitations.extend(result.warnings or ["Real incident runtime evidence is unavailable."])

        limitations.extend(result.warnings)
        limitations = list(dict.fromkeys(limitations))
        for limitation in limitations:
            claims.append(
                Claim(
                    id=f"C{cid:02d}",
                    kind=ClaimKind.LIMITATION,
                    text=limitation,
                    evidence="DEFERRED",
                    runtime_observed=False,
                )
            )
            cid += 1

        return ResponseEnvelope(
            question=question,
            intent="OPERATIONAL_INCIDENT_DRILLDOWN",
            status=self._answer_status(result.status),
            subject={"kind": "metric", "id": metric} if metric else {},
            claims=claims,
            limitations=limitations,
            evidence_levels=sorted({claim.evidence for claim in claims}),
        )

    @staticmethod
    def _answer_status(status: IncidentDrilldownStatus) -> AnswerStatus:
        return {
            IncidentDrilldownStatus.COMPLETE: AnswerStatus.ANSWERED,
            IncidentDrilldownStatus.NO_INCIDENT: AnswerStatus.ANSWERED,
            IncidentDrilldownStatus.PARTIAL: AnswerStatus.PARTIAL,
            IncidentDrilldownStatus.DEFERRED: AnswerStatus.DEFERRED,
            IncidentDrilldownStatus.BLOCKED: AnswerStatus.BLOCKED,
            IncidentDrilldownStatus.ERROR: AnswerStatus.ERROR,
        }[status]
