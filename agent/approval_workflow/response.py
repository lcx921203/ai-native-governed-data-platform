from __future__ import annotations

from pathlib import Path

from agent.response import AnswerStatus, Claim, ClaimKind, ResponseEnvelope

from .contracts import ApprovalWorkflowBundle, ApprovalWorkflowStatus


class ApprovalWorkflowEvidenceComposer:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()

    def compose(self, question: str, metric: str | None, bundle: ApprovalWorkflowBundle) -> ResponseEnvelope:
        claims: list[Claim] = []
        limitations = list(bundle.warnings)

        def add(kind, text, *, evidence="STATIC_CONTRACT", runtime=False, locations=()):
            claims.append(
                Claim(
                    id=f"C{len(claims)+1:02d}",
                    kind=kind,
                    text=text,
                    evidence=evidence,
                    runtime_observed=runtime,
                    source_locations=tuple(locations),
                )
            )

        for case in bundle.cases[:8]:
            request = case.request
            add(
                ClaimKind.APPROVAL_STATUS,
                (
                    f"Approval {request.approval_id}: status={case.status.value}; partition={request.partition_key}; "
                    f"action={request.action}; authority={request.authority}; expires_at={request.expires_at}; "
                    "agent_execution_allowed=false."
                ),
                evidence=bundle.evidence,
                runtime=False,
                locations=(
                    "agent/contracts/approval_workflow_policy.yml",
                    "agent/approval_workflow/workflow.py",
                ),
            )
            add(
                ClaimKind.APPROVAL_AUDIT,
                (
                    f"Approval audit {request.approval_id}: events={len(case.events)}; "
                    f"latest_event_hash={case.events[-1].event_hash}; evidence_fingerprint={request.evidence_fingerprint}."
                ),
                evidence="STATIC_CONTRACT",
                runtime=False,
                locations=("agent/approval_workflow/contracts.py",),
            )

        limitations.extend([
            "APPROVED is not EXECUTED: a valid approval only authorizes an external governed execution authority to proceed after current-truth revalidation.",
            "The Agent has no production recovery/backfill execution authority and cannot self-approve.",
            "The SHA-256 audit chain is tamper-evident engineering metadata; it is not an identity signature or immutable audit-store guarantee.",
        ])
        limitations = list(dict.fromkeys(limitations))
        for item in limitations:
            add(ClaimKind.LIMITATION, item, evidence="DEFERRED")

        return ResponseEnvelope(
            question=question,
            intent="INCIDENT_APPROVAL_WORKFLOW",
            status=self._answer_status(bundle.status),
            subject={"kind": "metric", "id": metric} if metric else {},
            claims=claims,
            limitations=limitations,
            evidence_levels=sorted({claim.evidence for claim in claims}),
        )

    @staticmethod
    def _answer_status(status: ApprovalWorkflowStatus) -> AnswerStatus:
        return {
            ApprovalWorkflowStatus.NO_APPROVAL_REQUIRED: AnswerStatus.ANSWERED,
            ApprovalWorkflowStatus.PENDING: AnswerStatus.PARTIAL,
            ApprovalWorkflowStatus.PARTIAL: AnswerStatus.PARTIAL,
            ApprovalWorkflowStatus.DEFERRED: AnswerStatus.DEFERRED,
            ApprovalWorkflowStatus.BLOCKED: AnswerStatus.BLOCKED,
            ApprovalWorkflowStatus.ERROR: AnswerStatus.ERROR,
        }[status]
