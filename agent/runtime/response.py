"""Analysis / Runtime Failure -> ResponseEnvelope 的适配层。

普通 Metric / Metadata / Knowledge 仍复用已有 GovernedResponseComposer；
这里只补原 Composer 尚未覆盖的 Analysis Execution 与 Runtime Preflight Failure。

这样 Claim Ledger（声明账本）仍然是所有回答的统一证据边界。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.analysis_planner import (
    AnalysisExecutionStatus,
    AnalysisPlanStatus,
    AnalysisUnitExecutionStatus,
    AnalysisUnitKind,
)
from agent.response import AnswerStatus, Claim, ClaimKind, ResponseEnvelope
from agent.validation import ValidationDecision


class GovernedRuntimeResponseComposer:
    """把 Analysis / Runtime preflight 结果投影为统一 ResponseEnvelope。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()

    def compose_analysis(self, route: Any, execution: Any) -> ResponseEnvelope:
        """把 AnalysisExecution 转成有限 Claim Ledger。"""

        status = self._analysis_answer_status(execution)
        claims: list[Claim] = []
        limitations: list[str] = []
        tool_trace: list[dict[str, Any]] = []
        evidence_levels: set[str] = set()
        cid = 1

        def add(
            kind: ClaimKind,
            text: str,
            *,
            evidence: str = "STATIC_CONTRACT",
            runtime_observed: bool = False,
            source_locations: tuple[str, ...] = (),
        ) -> None:
            nonlocal cid
            # Answer Validator 有 20 条 claim 的硬限制；Runtime 再留出安全余量。
            if len(claims) >= 16:
                return
            claims.append(
                Claim(
                    id=f"C{cid:02d}",
                    kind=kind,
                    text=text,
                    evidence=evidence,
                    source_locations=source_locations,
                    runtime_observed=runtime_observed,
                )
            )
            cid += 1

        plan = execution.plan
        comparison = plan.comparison.to_dict() if plan.comparison else {}
        add(
            ClaimKind.SEMANTIC_QUERY_PLAN,
            (
                "Governed analysis plan: "
                f"skill={plan.skill_id}; target_metric={plan.target_metric}; "
                f"comparison={comparison.get('mode')}; units={len(plan.units)}."
            ),
            source_locations=(
                "skills/sales/decline_analysis.yml",
                "agent/contracts/analysis_planner_policy.yml",
            ),
        )

        for unit_result in execution.unit_results:
            evidence_levels.add(unit_result.evidence)
            tool_trace.append(
                {
                    "tool": f"analysis:{unit_result.kind.value}",
                    "unit_id": unit_result.unit_id,
                    "status": unit_result.status.value,
                    "evidence": unit_result.evidence,
                    "attempt": unit_result.attempt,
                }
            )

            if (
                unit_result.status is AnalysisUnitExecutionStatus.COMPLETE
                and unit_result.kind is AnalysisUnitKind.TIME_COMPARISON
                and unit_result.evidence == "RUNTIME_VERIFIED"
            ):
                payload = self._payload(unit_result.result)
                rows = list(payload.get("rows") or [])
                add(
                    ClaimKind.QUERY_RESULT,
                    (
                        f"Verified comparison [{unit_result.unit_id}] returned "
                        f"{len(rows)} row(s): {rows[:3]}"
                    ),
                    evidence="RUNTIME_VERIFIED",
                    runtime_observed=True,
                )

            elif (
                unit_result.status is AnalysisUnitExecutionStatus.COMPLETE
                and unit_result.kind is AnalysisUnitKind.BREAKDOWN
                and unit_result.evidence == "RUNTIME_VERIFIED"
            ):
                payload = self._payload(unit_result.result)
                rows = list(payload.get("rows") or [])
                add(
                    ClaimKind.DRIVER_ATTRIBUTION,
                    (
                        f"Verified driver lens [{unit_result.unit_id}] top evidence: "
                        f"{rows[:3]}"
                    ),
                    evidence="RUNTIME_VERIFIED",
                    runtime_observed=True,
                )

            elif (
                unit_result.status is AnalysisUnitExecutionStatus.COMPLETE
                and unit_result.kind is AnalysisUnitKind.EVIDENCE_SUMMARY
            ):
                payload = self._payload(unit_result.result)
                strongest = list(payload.get("strongest_drivers") or [])
                if strongest:
                    add(
                        ClaimKind.DRIVER_ATTRIBUTION,
                        (
                            "Evidence-only driver summary: "
                            f"{strongest[:3]}. "
                            "These are observed driver signals, not causal proof."
                        ),
                        evidence="DERIVED_VERIFIED",
                        runtime_observed=False,
                    )

            if unit_result.status is not AnalysisUnitExecutionStatus.COMPLETE:
                for warning in unit_result.warnings or [
                    f"Analysis unit {unit_result.unit_id} did not complete."
                ]:
                    if warning not in limitations:
                        limitations.append(warning)

        validation = execution.validation_result
        if validation is not None:
            evidence_levels.add(str(getattr(validation, "evidence", "STATIC_CONTRACT")))
            for issue in getattr(validation, "issues", ()) or ():
                text = f"{issue.code}: {issue.message}"
                if text not in limitations:
                    limitations.append(text)

            decision = getattr(validation, "decision", None)
            if decision is ValidationDecision.RETRY:
                limitations.append(
                    "Validation still requested RETRY after the bounded retry loop; no final trusted analysis claim may be added."
                )

        if status in {
            AnswerStatus.DEFERRED,
            AnswerStatus.BLOCKED,
            AnswerStatus.ERROR,
        } and not limitations:
            limitations.append(
                "Analysis did not reach a fully validated runtime result; no unsupported conclusion is produced."
            )

        limitations = list(dict.fromkeys(limitations))[:8]
        for limitation in limitations:
            add(
                ClaimKind.LIMITATION,
                limitation,
                evidence="DEFERRED",
            )

        return ResponseEnvelope(
            question=route.question,
            intent=route.intent.value,
            status=status,
            subject={
                "kind": route.target_kind,
                "id": route.target_id,
                "matched_alias": route.target_match,
                "skill_id": plan.skill_id,
            },
            claims=claims,
            limitations=limitations,
            sources=[
                {
                    "kind": "analytics_skill",
                    "location": "skills/sales/decline_analysis.yml",
                    "owner": "governed_agent",
                },
                {
                    "kind": "analysis_validation",
                    "location": "agent/contracts/analysis_validation_policy.yml",
                    "owner": "governed_agent",
                },
            ],
            tool_trace=tool_trace,
            evidence_levels=sorted(x for x in evidence_levels if x),
        )

    def compose_analysis_plan_failure(
        self,
        route: Any,
        plan: Any,
    ) -> ResponseEnvelope:
        """Analysis Planner 未 READY 时，不进入 Executor，直接形成澄清/阻断 Envelope。"""

        status_map = {
            AnalysisPlanStatus.CLARIFICATION_REQUIRED: AnswerStatus.CLARIFICATION_REQUIRED,
            AnalysisPlanStatus.BLOCKED: AnswerStatus.BLOCKED,
            AnalysisPlanStatus.ERROR: AnswerStatus.ERROR,
        }
        status = status_map.get(plan.status, AnswerStatus.BLOCKED)
        limitations = list(dict.fromkeys(plan.warnings or ["Analysis plan is not executable."]))[:8]

        claims: list[Claim] = []
        for index, warning in enumerate(limitations, start=1):
            kind = (
                ClaimKind.CLARIFICATION_REQUEST
                if status is AnswerStatus.CLARIFICATION_REQUIRED
                else ClaimKind.LIMITATION
            )
            claims.append(
                Claim(
                    id=f"C{index:02d}",
                    kind=kind,
                    text=warning,
                    evidence="STATIC_CONTRACT",
                )
            )

        return ResponseEnvelope(
            question=route.question,
            intent=route.intent.value,
            status=status,
            subject={
                "kind": route.target_kind,
                "id": route.target_id,
                "matched_alias": route.target_match,
                "skill_id": getattr(plan, "skill_id", None),
            },
            claims=claims,
            limitations=limitations,
            sources=[],
            tool_trace=[],
            evidence_levels=["STATIC_CONTRACT"],
        )

    def compose_preflight_failure(
        self,
        route: Any,
        *,
        status: AnswerStatus,
        warnings: list[str] | tuple[str, ...],
    ) -> ResponseEnvelope:
        """Context Loader 等 Planner 前置门禁失败时的统一 Fail-Closed 响应。"""

        limitations = list(dict.fromkeys(str(x) for x in warnings if str(x)))[:8]
        if not limitations:
            limitations = ["Governed runtime preflight did not pass."]

        claims = [
            Claim(
                id=f"C{index:02d}",
                kind=ClaimKind.LIMITATION,
                text=text,
                evidence="STATIC_CONTRACT",
            )
            for index, text in enumerate(limitations, start=1)
        ]

        return ResponseEnvelope(
            question=route.question,
            intent=route.intent.value,
            status=status,
            subject={
                "kind": route.target_kind,
                "id": route.target_id,
                "matched_alias": route.target_match,
            },
            claims=claims,
            limitations=limitations,
            sources=[],
            tool_trace=[],
            evidence_levels=["STATIC_CONTRACT"],
        )

    @staticmethod
    def _payload(value: Any) -> dict[str, Any]:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        return dict(value or {}) if isinstance(value, dict) else {}

    @staticmethod
    def _analysis_answer_status(execution: Any) -> AnswerStatus:
        validation = execution.validation_result
        if validation is not None:
            decision = getattr(validation, "decision", None)
            if decision is ValidationDecision.BLOCKED:
                return AnswerStatus.BLOCKED
            if decision is ValidationDecision.RETRY:
                return AnswerStatus.BLOCKED

        mapping = {
            AnalysisExecutionStatus.COMPLETE: AnswerStatus.ANSWERED,
            AnalysisExecutionStatus.PARTIAL: AnswerStatus.PARTIAL,
            AnalysisExecutionStatus.DEFERRED: AnswerStatus.DEFERRED,
            AnalysisExecutionStatus.BLOCKED: AnswerStatus.BLOCKED,
            AnalysisExecutionStatus.ERROR: AnswerStatus.ERROR,
        }
        return mapping.get(execution.status, AnswerStatus.ERROR)
