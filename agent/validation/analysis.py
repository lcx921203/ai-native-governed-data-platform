"""Governed Analysis Validation（受治理分析结果验证）。

这层验证的是“分析执行结果是否足够可信，可以进入 Claim Ledger / Answer Renderer”，
不是最终自然语言答案的 Evidence Validator。两者职责不同：

Analysis Validator:
    MetricFlow / Breakdown 执行结果 -> PASS / RETRY / BLOCKED

Answer Validator:
    Claim Ledger + LLM Draft -> 最终回答证据约束
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent.analysis_planner.contracts import (
    AnalysisExecution,
    AnalysisPlan,
    AnalysisPlanStatus,
    AnalysisUnit,
    AnalysisUnitExecutionStatus,
    AnalysisUnitKind,
)

from .contracts import (
    AnalysisValidationResult,
    ValidationDecision,
    ValidationIssue,
    ValidationSeverity,
)


class GovernedAnalysisValidator:
    """校验必需单元、Runtime Evidence、Summary 边界与 Retry 条件。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/analysis_validation_policy.yml").read_text(encoding="utf-8")
        )

    def validate(
        self,
        plan: AnalysisPlan,
        execution: AnalysisExecution,
        *,
        retry_budget_remaining: int = 0,
    ) -> AnalysisValidationResult:
        """输出 PASS / RETRY / BLOCKED，不修改原执行结果。"""

        if plan.status is not AnalysisPlanStatus.READY or not plan.executable:
            return self._blocked("PLAN_NOT_READY", "Validation requires an executable READY AnalysisPlan.")
        if execution.plan is not plan:
            return self._blocked("PLAN_EXECUTION_MISMATCH", "Execution does not belong to the supplied AnalysisPlan.")

        result_map = {item.unit_id: item for item in execution.unit_results}
        unit_map = {unit.unit_id: unit for unit in plan.units}
        issues: list[ValidationIssue] = []
        retryable_ids: set[str] = set()

        # 第一遍先定位“真正失败且可重试”的 required unit。
        for unit in plan.units:
            result = result_map.get(unit.unit_id)
            if not unit.required or result is None:
                continue
            if result.status.value in set(self.policy["retryable_statuses"]):
                retryable_ids.add(unit.unit_id)

        for unit in plan.units:
            result = result_map.get(unit.unit_id)
            if result is None:
                issues.append(
                    ValidationIssue(
                        code="MISSING_UNIT_RESULT",
                        message="Analysis unit has no execution result.",
                        severity=ValidationSeverity.ERROR if unit.required else ValidationSeverity.WARNING,
                        unit_id=unit.unit_id,
                    )
                )
                continue

            # Retryable upstream 失败导致的 downstream SKIPPED，在有 retry budget 时不要提前判死刑。
            if (
                unit.required
                and result.status is AnalysisUnitExecutionStatus.SKIPPED
                and retry_budget_remaining > 0
                and self._depends_on_any(unit, unit_map, retryable_ids)
            ):
                issues.append(
                    ValidationIssue(
                        code="SKIPPED_BEHIND_RETRYABLE_DEPENDENCY",
                        message="Unit is skipped behind a retryable required dependency and will be retried with it.",
                        severity=ValidationSeverity.WARNING,
                        unit_id=unit.unit_id,
                    )
                )
                continue

            if result.status is not AnalysisUnitExecutionStatus.COMPLETE:
                if not unit.required and bool(self.policy["principles"]["optional_failure_allows_partial"]):
                    issues.append(
                        ValidationIssue(
                            code="OPTIONAL_UNIT_INCOMPLETE",
                            message=f"Optional analysis unit ended with {result.status.value}; result can remain partial.",
                            severity=ValidationSeverity.WARNING,
                            unit_id=unit.unit_id,
                        )
                    )
                    continue

                retryable = result.status.value in set(self.policy["retryable_statuses"])
                issues.append(
                    ValidationIssue(
                        code="REQUIRED_UNIT_INCOMPLETE",
                        message=f"Required analysis unit ended with {result.status.value}.",
                        severity=ValidationSeverity.ERROR,
                        unit_id=unit.unit_id,
                        retryable=retryable,
                    )
                )
                continue

            if unit.kind in {AnalysisUnitKind.TIME_COMPARISON, AnalysisUnitKind.BREAKDOWN}:
                if result.evidence != str(self.policy["evidence"]["runtime_required"]):
                    issues.append(
                        ValidationIssue(
                            code="RUNTIME_EVIDENCE_REQUIRED",
                            message=(
                                f"{unit.kind.value} completed without required runtime evidence: "
                                f"{result.evidence}."
                            ),
                            severity=ValidationSeverity.ERROR if unit.required else ValidationSeverity.WARNING,
                            unit_id=unit.unit_id,
                        )
                    )
                if not result.validation:
                    issues.append(
                        ValidationIssue(
                            code="MISSING_SUBVALIDATION",
                            message="Runtime analysis result did not carry its own validation marker.",
                            severity=ValidationSeverity.ERROR if unit.required else ValidationSeverity.WARNING,
                            unit_id=unit.unit_id,
                        )
                    )

            if unit.kind is AnalysisUnitKind.EVIDENCE_SUMMARY:
                issues.extend(self._validate_summary(unit, result))

        errors = [item for item in issues if item.severity is ValidationSeverity.ERROR]
        retry_errors = [item for item in errors if item.retryable]
        hard_errors = [item for item in errors if not item.retryable]

        if hard_errors:
            return AnalysisValidationResult(
                decision=ValidationDecision.BLOCKED,
                issues=tuple(issues),
                retry_unit_ids=(),
                checked_units=len(plan.units),
                evidence="STATIC_CONTRACT",
            )

        if retry_errors:
            ids = tuple(dict.fromkeys(item.unit_id for item in retry_errors if item.unit_id))
            if retry_budget_remaining > 0:
                return AnalysisValidationResult(
                    decision=ValidationDecision.RETRY,
                    issues=tuple(issues),
                    retry_unit_ids=ids,
                    checked_units=len(plan.units),
                    evidence="STATIC_CONTRACT",
                    warnings=[f"Validation requested bounded retry; remaining budget={retry_budget_remaining}."],
                )
            return AnalysisValidationResult(
                decision=ValidationDecision.BLOCKED,
                issues=tuple(issues),
                retry_unit_ids=(),
                checked_units=len(plan.units),
                evidence="STATIC_CONTRACT",
                warnings=["Retryable failure remained after the governed retry budget was exhausted."],
            )

        return AnalysisValidationResult(
            decision=ValidationDecision.PASS,
            issues=tuple(issues),
            retry_unit_ids=(),
            checked_units=len(plan.units),
            evidence="RUNTIME_VERIFIED",
        )

    def _validate_summary(self, unit: AnalysisUnit, result: Any) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        expected = str(self.policy["evidence"]["summary_required"])
        if result.evidence != expected:
            issues.append(
                ValidationIssue(
                    code="SUMMARY_EVIDENCE_INVALID",
                    message=f"Evidence summary requires {expected}; got {result.evidence}.",
                    severity=ValidationSeverity.ERROR,
                    unit_id=unit.unit_id,
                )
            )

        compiled = unit.compiled_plan if isinstance(unit.compiled_plan, dict) else {}
        if compiled.get("evidence_only") is not True or compiled.get("no_new_metric_math") is not True:
            issues.append(
                ValidationIssue(
                    code="SUMMARY_GUARDRAIL_INVALID",
                    message="Evidence summary must be evidence_only and no_new_metric_math.",
                    severity=ValidationSeverity.ERROR,
                    unit_id=unit.unit_id,
                )
            )

        payload = result.result if isinstance(result.result, dict) else {}
        if payload.get("no_new_metric_math") is not True or payload.get("causal_claim_allowed") is not False:
            issues.append(
                ValidationIssue(
                    code="SUMMARY_PAYLOAD_GUARDRAIL_INVALID",
                    message="Summary payload violated no-new-math / no-causal-claim guardrail.",
                    severity=ValidationSeverity.ERROR,
                    unit_id=unit.unit_id,
                )
            )
        if not payload.get("evidence_units"):
            issues.append(
                ValidationIssue(
                    code="SUMMARY_WITHOUT_EVIDENCE",
                    message="Evidence summary contains no verified source units.",
                    severity=ValidationSeverity.ERROR,
                    unit_id=unit.unit_id,
                )
            )
        return issues

    @staticmethod
    def _depends_on_any(
        unit: AnalysisUnit,
        unit_map: dict[str, AnalysisUnit],
        targets: set[str],
    ) -> bool:
        """判断当前 Unit 是否直接/间接依赖任一 retryable target。"""

        seen: set[str] = set()
        stack = list(unit.depends_on)
        while stack:
            dependency_id = stack.pop()
            if dependency_id in seen:
                continue
            seen.add(dependency_id)
            if dependency_id in targets:
                return True
            dependency = unit_map.get(dependency_id)
            if dependency:
                stack.extend(dependency.depends_on)
        return False

    @staticmethod
    def _blocked(code: str, message: str) -> AnalysisValidationResult:
        return AnalysisValidationResult(
            decision=ValidationDecision.BLOCKED,
            issues=(
                ValidationIssue(
                    code=code,
                    message=message,
                    severity=ValidationSeverity.ERROR,
                ),
            ),
            checked_units=0,
            evidence="STATIC_CONTRACT",
        )
