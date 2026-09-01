"""Governed Analysis Executor（受治理分析执行器）。

执行链路：
    AnalysisPlan -> bounded unit execution -> Analysis Validation -> bounded retry

关键边界：
1. 只执行 Analysis Planner 已编译的 TIME_COMPARISON / BREAKDOWN / EVIDENCE_SUMMARY；
2. TIME_COMPARISON / BREAKDOWN 继续调用既有 MetricFlow 受治理执行能力；
3. EVIDENCE_SUMMARY 只汇总已经验证的执行证据，不调用 LLM 发明新事实；
4. Retry 只针对 Validation 明确标记为 retryable 的单元，且有固定上限；
5. DEFERRED / BLOCKED / CLARIFICATION_REQUIRED 不做盲目重试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from agent.breakdown_analysis import GovernedComparativeBreakdown
from agent.time_context import GovernedTimeComparator

from .contracts import (
    AnalysisExecution,
    AnalysisExecutionStatus,
    AnalysisPlan,
    AnalysisPlanStatus,
    AnalysisUnit,
    AnalysisUnitExecution,
    AnalysisUnitExecutionStatus,
    AnalysisUnitKind,
)


class GovernedAnalysisExecutor:
    """按依赖顺序执行受治理 Analysis Units，并支持 Validation 驱动的有限重试。"""

    def __init__(
        self,
        project_root: Path | str,
        *,
        comparator: Any | None = None,
        breakdown: Any | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/analysis_executor_policy.yml").read_text(encoding="utf-8")
        )
        self.comparator = comparator or GovernedTimeComparator(self.root)
        self.breakdown = breakdown or GovernedComparativeBreakdown(self.root)

    def execute(self, plan: AnalysisPlan) -> AnalysisExecution:
        """执行一次 Analysis Plan，不自动 Retry；适合测试或上层 Runtime 自己编排。"""

        if plan.status is not AnalysisPlanStatus.READY or not plan.executable:
            return AnalysisExecution(
                plan=plan,
                status=AnalysisExecutionStatus.BLOCKED,
                warnings=["Analysis Executor requires an executable READY AnalysisPlan."],
            )

        results = self._execute_round(plan, existing={}, rerun_ids=None, attempts={})
        return AnalysisExecution(
            plan=plan,
            status=self._execution_status(plan, results),
            unit_results=tuple(results[unit.unit_id] for unit in plan.units if unit.unit_id in results),
            warnings=[],
        )

    def execute_with_validation(self, plan: AnalysisPlan, *, validator: Any | None = None) -> AnalysisExecution:
        """执行 + Validation + bounded retry 的闭环入口。

        Validation 决定 PASS / RETRY / BLOCKED；Executor 不自己猜哪些错误值得重试。
        """

        if plan.status is not AnalysisPlanStatus.READY or not plan.executable:
            return AnalysisExecution(
                plan=plan,
                status=AnalysisExecutionStatus.BLOCKED,
                warnings=["Analysis Executor requires an executable READY AnalysisPlan."],
            )

        # 延迟导入，避免 analysis_planner package 初始化时与 validation package 形成循环依赖。
        if validator is None:
            from agent.validation import GovernedAnalysisValidator

            validator = GovernedAnalysisValidator(self.root)

        max_retries = int(self.policy["limits"]["max_validation_retries"])
        attempts: dict[str, int] = {}
        results: dict[str, AnalysisUnitExecution] = {}
        rerun_ids: set[str] | None = None

        for retry_round in range(max_retries + 1):
            results = self._execute_round(
                plan,
                existing=results,
                rerun_ids=rerun_ids,
                attempts=attempts,
            )
            execution = AnalysisExecution(
                plan=plan,
                status=self._execution_status(plan, results),
                unit_results=tuple(results[unit.unit_id] for unit in plan.units if unit.unit_id in results),
                retry_rounds=retry_round,
            )
            validation = validator.validate(
                plan,
                execution,
                retry_budget_remaining=max_retries - retry_round,
            )
            execution.validation_result = validation

            if validation.decision.value != "RETRY":
                return execution

            rerun_ids = self._expand_dependents(plan, set(validation.retry_unit_ids))
            if not rerun_ids:
                return execution

        # 理论上循环会在最后一轮由 validator 产出 BLOCKED；这里仅做防御。
        return execution

    def _execute_round(
        self,
        plan: AnalysisPlan,
        *,
        existing: dict[str, AnalysisUnitExecution],
        rerun_ids: set[str] | None,
        attempts: dict[str, int],
    ) -> dict[str, AnalysisUnitExecution]:
        """按 Plan 顺序执行一轮；Retry 轮只重跑被选中的单元及其依赖后继。"""

        results = dict(existing)
        unit_map = {unit.unit_id: unit for unit in plan.units}

        for unit in plan.units:
            if rerun_ids is not None and unit.unit_id not in rerun_ids:
                continue

            blocker = self._required_dependency_blocker(unit, unit_map, results)
            if blocker:
                results[unit.unit_id] = AnalysisUnitExecution(
                    unit_id=unit.unit_id,
                    kind=unit.kind,
                    required=unit.required,
                    status=AnalysisUnitExecutionStatus.SKIPPED,
                    evidence="STATIC_CONTRACT",
                    warnings=[f"Required dependency is not COMPLETE: {blocker}"],
                    validation="DEPENDENCY_NOT_COMPLETE",
                    attempt=attempts.get(unit.unit_id, 0),
                )
                continue

            attempts[unit.unit_id] = attempts.get(unit.unit_id, 0) + 1
            results[unit.unit_id] = self._execute_unit(
                plan,
                unit,
                results,
                attempt=attempts[unit.unit_id],
            )

        return results

    def _execute_unit(
        self,
        plan: AnalysisPlan,
        unit: AnalysisUnit,
        results: dict[str, AnalysisUnitExecution],
        *,
        attempt: int,
    ) -> AnalysisUnitExecution:
        """执行一个 Planner 已批准的有限 Unit。"""

        if unit.kind is AnalysisUnitKind.TIME_COMPARISON:
            result = self.comparator.execute(unit.compiled_plan)
            return self._from_runtime_result(unit, result, attempt=attempt)

        if unit.kind is AnalysisUnitKind.BREAKDOWN:
            result = self.breakdown.execute(unit.compiled_plan)
            return self._from_runtime_result(unit, result, attempt=attempt)

        if unit.kind is AnalysisUnitKind.EVIDENCE_SUMMARY:
            payload = self._evidence_summary(plan, unit, results)
            if not payload["evidence_units"]:
                return AnalysisUnitExecution(
                    unit_id=unit.unit_id,
                    kind=unit.kind,
                    required=unit.required,
                    status=AnalysisUnitExecutionStatus.BLOCKED,
                    evidence="STATIC_CONTRACT",
                    result=payload,
                    warnings=["No verified execution evidence is available for summary."],
                    validation="NO_VERIFIED_EVIDENCE",
                    attempt=attempt,
                )
            return AnalysisUnitExecution(
                unit_id=unit.unit_id,
                kind=unit.kind,
                required=unit.required,
                status=AnalysisUnitExecutionStatus.COMPLETE,
                evidence="DERIVED_VERIFIED",
                result=payload,
                warnings=[],
                validation="EVIDENCE_ONLY_SUMMARY",
                attempt=attempt,
            )

        return AnalysisUnitExecution(
            unit_id=unit.unit_id,
            kind=unit.kind,
            required=unit.required,
            status=AnalysisUnitExecutionStatus.BLOCKED,
            evidence="STATIC_CONTRACT",
            warnings=[f"Unsupported governed AnalysisUnitKind: {unit.kind.value}"],
            validation="UNSUPPORTED_UNIT_KIND",
            attempt=attempt,
        )

    @staticmethod
    def _from_runtime_result(unit: AnalysisUnit, result: Any, *, attempt: int) -> AnalysisUnitExecution:
        """把既有 MetricFlow/Breakdown 结果状态映射到统一 Analysis Unit 状态。"""

        raw_status = str(getattr(getattr(result, "status", None), "value", getattr(result, "status", "ERROR")))
        mapping = {
            "COMPLETE": AnalysisUnitExecutionStatus.COMPLETE,
            "DEFERRED": AnalysisUnitExecutionStatus.DEFERRED,
            "BLOCKED": AnalysisUnitExecutionStatus.BLOCKED,
            "ERROR": AnalysisUnitExecutionStatus.ERROR,
            "CLARIFICATION_REQUIRED": AnalysisUnitExecutionStatus.CLARIFICATION_REQUIRED,
        }
        return AnalysisUnitExecution(
            unit_id=unit.unit_id,
            kind=unit.kind,
            required=unit.required,
            status=mapping.get(raw_status, AnalysisUnitExecutionStatus.ERROR),
            evidence=str(getattr(result, "evidence", "STATIC_CONTRACT")),
            result=result,
            warnings=list(getattr(result, "warnings", []) or []),
            validation=str(getattr(result, "validation", "")),
            attempt=attempt,
        )

    def _evidence_summary(
        self,
        plan: AnalysisPlan,
        unit: AnalysisUnit,
        results: dict[str, AnalysisUnitExecution],
    ) -> dict[str, Any]:
        """只从已验证依赖提炼结构化摘要，不做新计算、不产生因果结论。"""

        evidence_units: list[dict[str, Any]] = []
        strongest_drivers: list[dict[str, Any]] = []

        for dependency_id in unit.depends_on:
            item = results.get(dependency_id)
            if item is None or item.status is not AnalysisUnitExecutionStatus.COMPLETE:
                continue
            if item.kind in {AnalysisUnitKind.TIME_COMPARISON, AnalysisUnitKind.BREAKDOWN} and item.evidence != "RUNTIME_VERIFIED":
                continue

            evidence_units.append(
                {
                    "unit_id": item.unit_id,
                    "kind": item.kind.value,
                    "evidence": item.evidence,
                    "validation": item.validation,
                }
            )

            if item.kind is AnalysisUnitKind.BREAKDOWN:
                payload = item.result.to_dict() if hasattr(item.result, "to_dict") else (item.result or {})
                rows = list(payload.get("rows") or []) if isinstance(payload, dict) else []
                if rows:
                    top = dict(rows[0])
                    top["source_unit_id"] = item.unit_id
                    strongest_drivers.append(top)

        return {
            "skill_id": plan.skill_id,
            "target_metric": plan.target_metric,
            "evidence_units": evidence_units,
            "strongest_drivers": strongest_drivers,
            "no_new_metric_math": True,
            "causal_claim_allowed": False,
            "summary_authority": "VERIFIED_ANALYSIS_EVIDENCE",
        }

    @staticmethod
    def _required_dependency_blocker(
        unit: AnalysisUnit,
        unit_map: dict[str, AnalysisUnit],
        results: dict[str, AnalysisUnitExecution],
    ) -> str | None:
        """仅 required 依赖失败时阻断；optional 依赖失败允许降级为 Partial Evidence。"""

        for dependency_id in unit.depends_on:
            dependency_unit = unit_map.get(dependency_id)
            dependency_result = results.get(dependency_id)
            if dependency_unit is None:
                return dependency_id
            if not dependency_unit.required:
                continue
            if dependency_result is None or dependency_result.status is not AnalysisUnitExecutionStatus.COMPLETE:
                return dependency_id
        return None

    @staticmethod
    def _expand_dependents(plan: AnalysisPlan, seeds: set[str]) -> set[str]:
        """Retry 一个失败单元时，同时重跑所有直接/间接依赖它的后继单元。"""

        expanded = set(seeds)
        changed = True
        while changed:
            changed = False
            for unit in plan.units:
                if unit.unit_id in expanded:
                    continue
                if any(dep in expanded for dep in unit.depends_on):
                    expanded.add(unit.unit_id)
                    changed = True
        return expanded

    @staticmethod
    def _execution_status(
        plan: AnalysisPlan,
        results: dict[str, AnalysisUnitExecution],
    ) -> AnalysisExecutionStatus:
        """从 Unit 状态派生整体执行状态；最终可信性仍由 Validator 判定。"""

        required = [results.get(unit.unit_id) for unit in plan.units if unit.required]
        if all(item is not None and item.status is AnalysisUnitExecutionStatus.COMPLETE for item in required):
            optional_failures = [
                results.get(unit.unit_id)
                for unit in plan.units
                if not unit.required
                and (
                    results.get(unit.unit_id) is None
                    or results[unit.unit_id].status is not AnalysisUnitExecutionStatus.COMPLETE
                )
            ]
            return AnalysisExecutionStatus.PARTIAL if optional_failures else AnalysisExecutionStatus.COMPLETE

        statuses = {item.status for item in required if item is not None}
        if AnalysisUnitExecutionStatus.CLARIFICATION_REQUIRED in statuses:
            return AnalysisExecutionStatus.BLOCKED
        if AnalysisUnitExecutionStatus.BLOCKED in statuses:
            return AnalysisExecutionStatus.BLOCKED
        if AnalysisUnitExecutionStatus.DEFERRED in statuses:
            return AnalysisExecutionStatus.DEFERRED
        return AnalysisExecutionStatus.ERROR
