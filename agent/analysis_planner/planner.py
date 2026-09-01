"""Governed Analysis Planner（受治理分析规划器）。

主链路：
    Router -> Context Planner -> Skill Registry -> Analysis Planner -> Analysis Executor

本模块解决的是“Skill 怎么变成可执行计划”，不是“LLM 自己想分析步骤”。
它复用现有 Semantic Query / Time Comparison / Comparative Breakdown 能力，
因此不会创建第二套 SQL、指标公式或 Join 逻辑。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agent.breakdown_analysis import BreakdownAnalysisMode, GovernedComparativeBreakdown
from agent.context import ContextPlan, ContextSource
from agent.router import Intent, PlanStatus
from agent.semantic_query import GovernedSemanticQueryPlanner, SemanticQueryStatus
from agent.skills import GovernedSkillRegistry, SkillResolutionStatus
from agent.time_context import ComparisonMode, GovernedTimeComparator, TimeComparisonContext

from .contracts import AnalysisPlan, AnalysisPlanStatus, AnalysisUnit, AnalysisUnitKind


class GovernedAnalysisPlanner:
    """把唯一命中的 ACTIVE Analytics Skill 编译为有限受治理执行单元。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/analysis_planner_policy.yml").read_text(encoding="utf-8")
        )
        self.skill_registry = GovernedSkillRegistry(self.root)
        self.semantic_planner = GovernedSemanticQueryPlanner(self.root)
        self.comparator = GovernedTimeComparator(self.root)
        self.breakdown = GovernedComparativeBreakdown(self.root)

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    def plan(self, route: Any, context_plan: ContextPlan) -> AnalysisPlan:
        """把 Router + Context Plan + Skill 编译成 AnalysisPlan。

        这里要求调用方明确传入 Context Planner 的结果，防止绕过：
        ``Router -> Context Planner -> Analysis Planner`` 的主链路。
        """

        question = str(getattr(route, "question", "") or "").strip()
        target_metric = str(getattr(route, "target_id", "") or "").strip()

        route_warning = self._validate_route(route)
        if route_warning:
            return self._stop(
                AnalysisPlanStatus.BLOCKED,
                question,
                target_metric=target_metric or None,
                warning=route_warning,
            )

        context_warning = self._validate_context(context_plan)
        if context_warning:
            return self._stop(
                AnalysisPlanStatus.BLOCKED,
                question,
                target_metric=target_metric,
                warning=context_warning,
            )

        resolution = self.skill_registry.resolve(route)
        if resolution.status is not SkillResolutionStatus.RESOLVED or resolution.skill is None:
            status = (
                AnalysisPlanStatus.CLARIFICATION_REQUIRED
                if resolution.status is SkillResolutionStatus.AMBIGUOUS
                else AnalysisPlanStatus.BLOCKED
            )
            warning = "; ".join(resolution.warnings) or "No unique governed Analytics Skill was resolved."
            return self._stop(status, question, target_metric=target_metric, warning=warning)

        skill = resolution.skill
        skill_warning = self._validate_skill(skill, target_metric)
        if skill_warning:
            return self._stop(
                AnalysisPlanStatus.BLOCKED,
                question,
                target_metric=target_metric,
                skill_id=skill.skill_id,
                warning=skill_warning,
            )

        comparison, comparison_warning = self._comparison_context(question)
        if comparison is None:
            return self._stop(
                AnalysisPlanStatus.CLARIFICATION_REQUIRED,
                question,
                target_metric=target_metric,
                skill_id=skill.skill_id,
                warning=comparison_warning,
            )

        # 先让既有 Semantic Planner 校验受治理 Metric、显式日期、过滤与范围。
        primary = self.semantic_planner.plan(metric=target_metric, question=question)
        if primary.status is not SemanticQueryStatus.READY or primary.spec is None:
            return self._from_semantic_failure(
                primary.status,
                question,
                target_metric,
                skill.skill_id,
                primary.warnings,
                comparison,
            )

        # Skill 负责分析路径，所以 aggregate baseline 不继承用户偶然写出的 group-by；
        # 过滤条件和时间范围仍来自 Semantic Planner 已验证的 primary spec。
        primary_spec = replace(primary.spec, group_by=())

        units: list[AnalysisUnit] = []
        previous_units: list[str] = []
        for index, step in enumerate(skill.analysis_steps, start=1):
            skill_step_id = str(step.get("id", "")).strip()
            action = str(step.get("action", "")).strip()
            purpose = str(step.get("purpose", "")).strip()
            unit_prefix = f"{index:02d}_{skill_step_id or action}"

            if action == "compare_target_metric":
                compiled = self.comparator.plan(
                    primary_spec,
                    context=comparison,
                    question=question,
                )
                failure = self._subplan_failure(compiled.status, compiled.warnings)
                if failure:
                    return self._stop(
                        failure[0], question, target_metric=target_metric, skill_id=skill.skill_id,
                        comparison=comparison, warning=f"{skill_step_id}: {failure[1]}"
                    )
                unit = AnalysisUnit(
                    unit_id=unit_prefix,
                    kind=AnalysisUnitKind.TIME_COMPARISON,
                    skill_step_id=skill_step_id,
                    required=True,
                    authority="MetricFlow",
                    compiled_plan=compiled,
                    purpose=purpose,
                )
                units.append(unit)
                previous_units.append(unit.unit_id)
                continue

            if action == "compare_companion_metrics":
                metrics = tuple(str(x) for x in step.get("metrics", ()))
                if not metrics or not set(metrics).issubset(set(skill.required_metrics)):
                    return self._invalid_skill_step(question, target_metric, skill.skill_id, comparison, skill_step_id,
                                                    "companion metrics must be a non-empty subset of requirements.required_metrics")
                semantic = self.semantic_planner.plan_metrics(metrics=metrics, question=question)
                if semantic.status is not SemanticQueryStatus.READY or semantic.spec is None:
                    return self._from_semantic_failure(
                        semantic.status, question, target_metric, skill.skill_id,
                        [f"{skill_step_id}: {w}" for w in semantic.warnings], comparison
                    )
                compiled = self.comparator.plan(
                    replace(semantic.spec, group_by=()),
                    context=comparison,
                    question=question,
                )
                failure = self._subplan_failure(compiled.status, compiled.warnings)
                if failure:
                    return self._stop(
                        failure[0], question, target_metric=target_metric, skill_id=skill.skill_id,
                        comparison=comparison, warning=f"{skill_step_id}: {failure[1]}"
                    )
                unit = AnalysisUnit(
                    unit_id=unit_prefix,
                    kind=AnalysisUnitKind.TIME_COMPARISON,
                    skill_step_id=skill_step_id,
                    required=True,
                    authority="MetricFlow",
                    compiled_plan=compiled,
                    depends_on=tuple(previous_units[-1:]),
                    purpose=purpose,
                )
                units.append(unit)
                previous_units.append(unit.unit_id)
                continue

            if action == "breakdown_by_dimensions":
                dimensions = tuple(str(x) for x in step.get("dimensions", ()))
                if not dimensions or not set(dimensions).issubset(set(skill.dimensions)):
                    return self._invalid_skill_step(question, target_metric, skill.skill_id, comparison, skill_step_id,
                                                    "breakdown dimensions must be a non-empty subset of requirements.dimensions")
                for dimension_index, dimension in enumerate(dimensions, start=1):
                    breakdown_spec = replace(primary_spec, group_by=(dimension,))
                    compiled = self.breakdown.plan(
                        breakdown_spec,
                        context=comparison,
                        question=question,
                        mode=BreakdownAnalysisMode.TOP_ABSOLUTE_CHANGE,
                    )
                    failure = self._subplan_failure(compiled.status, compiled.warnings)
                    if failure:
                        return self._stop(
                            failure[0], question, target_metric=target_metric, skill_id=skill.skill_id,
                            comparison=comparison,
                            warning=f"{skill_step_id}/{dimension}: {failure[1]}",
                        )
                    unit = AnalysisUnit(
                        unit_id=f"{unit_prefix}_{dimension_index:02d}",
                        kind=AnalysisUnitKind.BREAKDOWN,
                        skill_step_id=skill_step_id,
                        required=True,
                        authority="MetricFlow",
                        compiled_plan=compiled,
                        depends_on=tuple(previous_units[:1]),
                        purpose=f"{purpose} [{dimension}]",
                    )
                    units.append(unit)
                    previous_units.append(unit.unit_id)
                continue

            if action == "compare_optional_metrics":
                metrics = tuple(str(x) for x in step.get("metrics", ()))
                if not metrics or not set(metrics).issubset(set(skill.optional_metrics)):
                    return self._invalid_skill_step(question, target_metric, skill.skill_id, comparison, skill_step_id,
                                                    "optional metrics must be a non-empty subset of requirements.optional_metrics")
                semantic = self.semantic_planner.plan_metrics(metrics=metrics, question=question)
                if semantic.status is not SemanticQueryStatus.READY or semantic.spec is None:
                    return self._from_semantic_failure(
                        semantic.status, question, target_metric, skill.skill_id,
                        [f"{skill_step_id}: {w}" for w in semantic.warnings], comparison
                    )
                compiled = self.comparator.plan(
                    replace(semantic.spec, group_by=()),
                    context=comparison,
                    question=question,
                )
                failure = self._subplan_failure(compiled.status, compiled.warnings)
                if failure:
                    return self._stop(
                        failure[0], question, target_metric=target_metric, skill_id=skill.skill_id,
                        comparison=comparison, warning=f"{skill_step_id}: {failure[1]}"
                    )
                unit = AnalysisUnit(
                    unit_id=unit_prefix,
                    kind=AnalysisUnitKind.TIME_COMPARISON,
                    skill_step_id=skill_step_id,
                    required=False,
                    authority="MetricFlow",
                    compiled_plan=compiled,
                    depends_on=tuple(previous_units[:1]),
                    purpose=purpose,
                )
                units.append(unit)
                previous_units.append(unit.unit_id)
                continue

            if action == "summarize_ranked_drivers":
                unit = AnalysisUnit(
                    unit_id=unit_prefix,
                    kind=AnalysisUnitKind.EVIDENCE_SUMMARY,
                    skill_step_id=skill_step_id,
                    required=True,
                    authority=str(self.policy["authority"]["summary"]),
                    compiled_plan={
                        "evidence_only": True,
                        "no_new_metric_math": True,
                        "no_causal_claim_without_evidence": True,
                    },
                    depends_on=tuple(previous_units),
                    purpose=purpose,
                )
                units.append(unit)
                previous_units.append(unit.unit_id)
                continue

            return self._invalid_skill_step(
                question,
                target_metric,
                skill.skill_id,
                comparison,
                skill_step_id or f"step_{index}",
                f"unsupported action={action!r}",
            )

        if not units:
            return self._stop(
                AnalysisPlanStatus.BLOCKED,
                question,
                target_metric=target_metric,
                skill_id=skill.skill_id,
                comparison=comparison,
                warning="Resolved Skill compiled to zero analysis units.",
            )

        return AnalysisPlan(
            status=AnalysisPlanStatus.READY,
            question=question,
            target_metric=target_metric,
            skill_id=skill.skill_id,
            comparison=comparison,
            units=tuple(units),
            warnings=[
                "Analysis plan is compiled from governed Skill + MetricFlow semantics; it has not executed any runtime query yet."
            ],
        )

    def _validate_route(self, route: Any) -> str | None:
        if getattr(route, "intent", None) is not Intent.ANALYSIS:
            return "Analysis Planner only accepts Router Intent.ANALYSIS."
        if getattr(route, "status", None) is not PlanStatus.PLANNING_REQUIRED:
            return "ANALYSIS route must be PLANNING_REQUIRED before Analysis Planner compiles Skill steps."
        if str(getattr(route, "target_kind", "") or "") != "metric":
            return "Analysis v1 requires one governed target metric."
        target_id = str(getattr(route, "target_id", "") or "")
        if not target_id or "," in target_id:
            return "Analysis v1 requires exactly one governed target metric."
        return None

    @staticmethod
    def _validate_context(context_plan: ContextPlan) -> str | None:
        if context_plan.route_intent != "ANALYSIS":
            return "Context Plan does not belong to ANALYSIS route."
        required = set(context_plan.required_sources())
        missing = {ContextSource.SEMANTIC, ContextSource.SKILL} - required
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            return f"ANALYSIS Context Plan is missing required source(s): {names}."
        return None

    def _validate_skill(self, skill: Any, target_metric: str) -> str | None:
        limits = self.policy["limits"]
        if len(skill.analysis_steps) > int(limits["max_skill_steps"]):
            return "Skill exceeds governed max_skill_steps."
        if len(skill.required_metrics) > int(limits["max_required_metrics"]):
            return "Skill exceeds governed max_required_metrics."
        if len(skill.optional_metrics) > int(limits["max_optional_metrics"]):
            return "Skill exceeds governed max_optional_metrics."
        if len(skill.dimensions) > int(limits["max_dimensions"]):
            return "Skill exceeds governed max_dimensions."
        if target_metric not in skill.metrics:
            return f"Skill {skill.skill_id} does not declare target metric {target_metric}."

        governed_metrics = set(self.semantic_planner.governed_metrics)
        declared_metrics = {target_metric, *skill.required_metrics, *skill.optional_metrics}
        ungoverned = sorted(declared_metrics - governed_metrics)
        if ungoverned:
            return "Skill references ungoverned metric(s): " + ", ".join(ungoverned)

        governed_dimensions = set(self.semantic_planner.policy.get("structured_filter_dimensions", {}))
        ungoverned_dimensions = sorted(set(skill.dimensions) - governed_dimensions)
        if ungoverned_dimensions:
            return "Skill references ungoverned dimension(s): " + ", ".join(ungoverned_dimensions)

        allowed_actions = set(self.policy["allowed_actions"])
        actions = [str(step.get("action", "")) for step in skill.analysis_steps]
        unsupported = sorted({action for action in actions if action not in allowed_actions})
        if unsupported:
            return "Skill references unsupported action(s): " + ", ".join(unsupported)

        if skill.authority.get("metric_definition") != "MetricFlow":
            return "Skill metric_definition authority must remain MetricFlow."
        if skill.authority.get("dimension_path") != "MetricFlow":
            return "Skill dimension_path authority must remain MetricFlow."
        return None

    def _comparison_context(self, question: str) -> tuple[TimeComparisonContext | None, str]:
        q = question.casefold()
        markers = self.policy["comparison_markers"]
        matches: list[ComparisonMode] = []
        if any(str(marker).casefold() in q for marker in markers["previous_period"]):
            matches.append(ComparisonMode.PREVIOUS_PERIOD)
        if any(str(marker).casefold() in q for marker in markers["year_over_year"]):
            matches.append(ComparisonMode.YEAR_OVER_YEAR)

        if len(matches) == 1:
            mode = matches[0]
            label = "previous_period" if mode is ComparisonMode.PREVIOUS_PERIOD else "year_over_year"
            return TimeComparisonContext(mode=mode, label=label), ""
        if len(matches) > 1:
            return None, "Both previous-period and year-over-year comparison markers were found; choose one comparison baseline."
        return None, (
            "Analysis Skill requires an explicit reference window. "
            "请明确使用环比/上一期，还是同比/去年同期；Planner 不会默认猜比较基线。"
        )

    @staticmethod
    def _subplan_failure(status: SemanticQueryStatus, warnings: list[str]) -> tuple[AnalysisPlanStatus, str] | None:
        if status is SemanticQueryStatus.READY:
            return None
        mapped = GovernedAnalysisPlanner._map_semantic_status(status)
        return mapped, "; ".join(warnings) or f"Sub-plan status={status.value}"

    @staticmethod
    def _map_semantic_status(status: SemanticQueryStatus) -> AnalysisPlanStatus:
        if status is SemanticQueryStatus.CLARIFICATION_REQUIRED:
            return AnalysisPlanStatus.CLARIFICATION_REQUIRED
        if status is SemanticQueryStatus.BLOCKED:
            return AnalysisPlanStatus.BLOCKED
        return AnalysisPlanStatus.ERROR

    def _from_semantic_failure(
        self,
        status: SemanticQueryStatus,
        question: str,
        target_metric: str,
        skill_id: str,
        warnings: list[str],
        comparison: TimeComparisonContext | None,
    ) -> AnalysisPlan:
        return self._stop(
            self._map_semantic_status(status),
            question,
            target_metric=target_metric,
            skill_id=skill_id,
            comparison=comparison,
            warning="; ".join(warnings) or f"Semantic planning status={status.value}",
        )

    def _invalid_skill_step(
        self,
        question: str,
        target_metric: str,
        skill_id: str,
        comparison: TimeComparisonContext,
        step_id: str,
        reason: str,
    ) -> AnalysisPlan:
        return self._stop(
            AnalysisPlanStatus.BLOCKED,
            question,
            target_metric=target_metric,
            skill_id=skill_id,
            comparison=comparison,
            warning=f"Invalid governed Skill step {step_id}: {reason}.",
        )

    @staticmethod
    def _stop(
        status: AnalysisPlanStatus,
        question: str,
        *,
        target_metric: str | None = None,
        skill_id: str | None = None,
        comparison: TimeComparisonContext | None = None,
        warning: str,
    ) -> AnalysisPlan:
        return AnalysisPlan(
            status=status,
            question=question,
            target_metric=target_metric,
            skill_id=skill_id,
            comparison=comparison,
            units=(),
            warnings=[warning],
        )
