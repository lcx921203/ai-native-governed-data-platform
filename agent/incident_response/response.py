from __future__ import annotations

from pathlib import Path

from agent.response import AnswerStatus, Claim, ClaimKind, ResponseEnvelope

from .contracts import IncidentResponsePlan, IncidentResponseStatus


class IncidentResponseEvidenceComposer:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()

    def compose(self, question: str, metric: str | None, plan: IncidentResponsePlan) -> ResponseEnvelope:
        claims: list[Claim] = []
        limitations: list[str] = []
        cid = 1

        def add(kind, text, *, evidence="STATIC_CONTRACT", runtime=False, locations=()):
            nonlocal cid
            claims.append(Claim(
                id=f"C{cid:02d}",
                kind=kind,
                text=text,
                evidence=evidence,
                source_locations=tuple(locations),
                runtime_observed=runtime,
            ))
            cid += 1

        if plan.evidence == "RUNTIME_VERIFIED":
            for part in plan.partitions[:3]:
                action_summary = ", ".join(step.action.value for step in part.steps)
                add(
                    ClaimKind.INCIDENT_RESPONSE_PLAN,
                    (
                        f"Incident response plan for partition {part.partition_key}: status={part.status.value}; "
                        f"phase3c_policy_action={part.policy_action}; policy_reason={part.policy_reason}; "
                        f"steps={action_summary}."
                    ),
                    evidence="RUNTIME_VERIFIED",
                    runtime=False,
                    locations=(
                        "agent/contracts/incident_response_policy.yml",
                        "agent/incident_response/planner.py",
                        "orchestration/dagster/commerce_dagster/recovery_policy.py",
                    ),
                )
                authorities = sorted({step.authority.value for step in part.steps})
                boundaries = sorted({step.approval_boundary.value for step in part.steps})
                add(
                    ClaimKind.ACTION_AUTHORITY,
                    (
                        f"Action authority for partition {part.partition_key}: authorities={','.join(authorities)}; "
                        f"approval_boundaries={','.join(boundaries)}; agent_execution_allowed=false."
                    ),
                    evidence="STATIC_CONTRACT",
                    runtime=False,
                    locations=("agent/contracts/incident_response_policy.yml",),
                )
        else:
            limitations.extend(plan.warnings or ["Runtime-verified incident evidence is unavailable for response planning."])

        limitations.extend(plan.warnings)
        limitations.append(
            "The Agent has no production recovery/backfill write authority in Phase 6E; AUTO_REPLAY is delegated to the existing Dagster Recovery Sensor and manual actions stop at a human approval boundary."
        )
        limitations = list(dict.fromkeys(limitations))
        for item in limitations:
            add(ClaimKind.LIMITATION, item, evidence="DEFERRED")

        return ResponseEnvelope(
            question=question,
            intent="INCIDENT_RESPONSE_PLANNING",
            status=self._answer_status(plan.status),
            subject={"kind": "metric", "id": metric} if metric else {},
            claims=claims,
            limitations=limitations,
            evidence_levels=sorted({c.evidence for c in claims}),
        )

    @staticmethod
    def _answer_status(status: IncidentResponseStatus) -> AnswerStatus:
        return {
            IncidentResponseStatus.NO_ACTION: AnswerStatus.ANSWERED,
            IncidentResponseStatus.WAITING: AnswerStatus.PARTIAL,
            IncidentResponseStatus.DELEGATED: AnswerStatus.PARTIAL,
            IncidentResponseStatus.HUMAN_ACTION_REQUIRED: AnswerStatus.PARTIAL,
            IncidentResponseStatus.PARTIAL: AnswerStatus.PARTIAL,
            IncidentResponseStatus.DEFERRED: AnswerStatus.DEFERRED,
            IncidentResponseStatus.BLOCKED: AnswerStatus.BLOCKED,
            IncidentResponseStatus.ERROR: AnswerStatus.ERROR,
        }[status]
