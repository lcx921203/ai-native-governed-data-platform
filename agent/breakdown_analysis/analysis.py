"""受治理的比较分解与贡献度分析。

引擎不直接生成 SQL，而是派生两个 bounded MetricFlow grouped queries，再按相同维度值对齐 current/reference。
工程边界：Contribution 只对声明为 additive 的 Metric 计算，不能跨独立 lens 相加。
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from agent.breakdown_analysis.contracts import (
    BreakdownAnalysisMode,
    ComparativeBreakdownPlan,
    ComparativeBreakdownResult,
    ComparativeBreakdownRow,
)
from agent.breakdown_analysis.semantics import MetricContributionSemantics
from agent.semantic_query import (
    MetricFlowSemanticQueryExecutor,
    SemanticQueryPlan,
    SemanticQuerySpec,
    SemanticQueryStatus,
)
from agent.time_context import ComparisonMode, TimeComparisonContext


class GovernedComparativeBreakdown:
    """对一个受治理维度执行 current/reference breakdown、change、ranking 与可选 contribution。"""
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/comparative_breakdown_policy.yml").read_text(encoding="utf-8")
        )
        self.semantic_policy = yaml.safe_load(
            (self.root / "agent/contracts/semantic_query_policy.yml").read_text(encoding="utf-8")
        )
        self.executor = MetricFlowSemanticQueryExecutor(self.root)
        self.metric_semantics = MetricContributionSemantics(self.root)

    def infer_mode(self, question: str) -> BreakdownAnalysisMode:
        """从问题标记推断 ranking / contribution 等有限分析模式；无法确定时保持安全默认。"""
        low = question.lower()
        markers = self.policy.get("markers", {})
        if any(marker.lower() in low for marker in markers.get("contribution", [])):
            return BreakdownAnalysisMode.CONTRIBUTION
        if any(marker.lower() in low for marker in markers.get("top_growth_rate", [])):
            return BreakdownAnalysisMode.TOP_GROWTH_RATE
        if any(marker.lower() in low for marker in markers.get("top_absolute_change", [])):
            return BreakdownAnalysisMode.TOP_ABSOLUTE_CHANGE
        return BreakdownAnalysisMode.COMPARE

    def plan(
        self,
        primary_spec: SemanticQuerySpec,
        *,
        context: TimeComparisonContext,
        question: str,
        mode: BreakdownAnalysisMode | None = None,
    ) -> ComparativeBreakdownPlan:
        """校验维度、比较窗口与 Metric 可分解语义，生成两个受治理 grouped query plan。"""
        mode = mode or self.infer_mode(question)
        metrics = primary_spec.metric_names
        if len(metrics) > int(self.policy["limits"]["max_metrics"]):
            return self._plan_stop(
                SemanticQueryStatus.BLOCKED, question, mode, context,
                f"Breakdown requested {len(metrics)} metrics; contract maximum is {self.policy['limits']['max_metrics']}.",
            )
        if mode is not BreakdownAnalysisMode.COMPARE and len(metrics) != 1:
            return self._plan_stop(
                SemanticQueryStatus.CLARIFICATION_REQUIRED, question, mode, context,
                "Ranking/contribution analysis requires exactly one governed metric. Choose the metric to analyze.",
            )

        non_time = tuple(item for item in primary_spec.group_by if not item.startswith("metric_time__"))
        if len(non_time) != 1:
            return self._plan_stop(
                SemanticQueryStatus.CLARIFICATION_REQUIRED, question, mode, context,
                "Comparative breakdown requires exactly one non-time group-by dimension; current dimensions: "
                + (", ".join(non_time) if non_time else "(none)"),
            )
        dimension = non_time[0]
        if dimension not in self.semantic_policy.get("structured_filter_dimensions", {}):
            return self._plan_stop(
                SemanticQueryStatus.BLOCKED, question, mode, context,
                f"Breakdown dimension is outside the governed dimension allowlist: {dimension}",
                dimension=dimension,
            )
        if mode is BreakdownAnalysisMode.CONTRIBUTION:
            metric = metrics[0]
            if not self.metric_semantics.is_additive(metric):
                return self._plan_stop(
                    SemanticQueryStatus.CLARIFICATION_REQUIRED, question, mode, context,
                    self.metric_semantics.reason(metric)
                    + " Contribution percentages would be misleading; use period comparison instead.",
                    dimension=dimension,
                )

        try:
            start = self._date(primary_spec.start_time)
            end = self._date(primary_spec.end_time)
        except ValueError as exc:
            return self._plan_stop(
                SemanticQueryStatus.BLOCKED, question, mode, context,
                f"Invalid governed primary time window: {exc}", dimension=dimension,
            )
        if end < start:
            return self._plan_stop(
                SemanticQueryStatus.BLOCKED, question, mode, context,
                "Primary semantic-query end date is earlier than start date.", dimension=dimension,
            )
        span_days = (end - start).days + 1
        if span_days > int(self.policy["limits"]["max_comparison_window_days"]):
            return self._plan_stop(
                SemanticQueryStatus.BLOCKED, question, mode, context,
                f"Comparison window is {span_days} days; contract maximum is {self.policy['limits']['max_comparison_window_days']}.",
                dimension=dimension,
            )
        if context.requested_days is not None and context.requested_days != span_days:
            return self._plan_stop(
                SemanticQueryStatus.CLARIFICATION_REQUIRED, question, mode, context,
                f"Primary window is {span_days} days but comparison requested {context.requested_days} days; equal-length windows are required.",
                dimension=dimension,
            )

        if context.mode is ComparisonMode.PREVIOUS_PERIOD:
            comparison_end = start - timedelta(days=1)
            comparison_start = comparison_end - timedelta(days=span_days - 1)
        elif context.mode is ComparisonMode.YEAR_OVER_YEAR:
            comparison_start = self._shift_year(start, -1)
            comparison_end = self._shift_year(end, -1)
        else:
            return self._plan_stop(
                SemanticQueryStatus.BLOCKED, question, mode, context,
                f"Unsupported comparison mode: {context.mode.value}", dimension=dimension,
            )

        limit = int(self.policy["limits"]["max_breakdown_members"])
        current = replace(primary_spec, group_by=(dimension,), limit=limit)
        comparison = replace(
            primary_spec,
            start_time=f"{comparison_start.isoformat()}T00:00:00Z",
            end_time=f"{comparison_end.isoformat()}T23:59:59Z",
            group_by=(dimension,),
            limit=limit,
        )
        warnings: list[str] = []
        if any(item.startswith("metric_time__") for item in primary_spec.group_by):
            warnings.append(
                "Breakdown comparison aggregates each full period by the governed business dimension; the session's temporal display grain is preserved but not used for change math."
            )
        return ComparativeBreakdownPlan(
            status=SemanticQueryStatus.READY,
            question=question,
            mode=mode,
            context=context,
            dimension=dimension,
            current_spec=current,
            comparison_spec=comparison,
            warnings=warnings,
        )

    def execute(self, plan: ComparativeBreakdownPlan) -> ComparativeBreakdownResult:
        """执行 current/reference grouped queries，只有底层证据满足要求才对齐维度值并派生 change/contribution。"""
        if plan.status is not SemanticQueryStatus.READY or not plan.current_spec or not plan.comparison_spec or not plan.dimension:
            return ComparativeBreakdownResult(
                status=plan.status,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=list(plan.warnings),
                validation="NOT_EXECUTED",
            )
        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=[*plan.warnings, f"Breakdown execution is disabled; set {gate}=true only in the intended runtime environment."],
                validation="NOT_EXECUTED",
            )

        current_result = self.executor.execute(self._query_plan(plan.current_spec, plan.question))
        if current_result.status is not SemanticQueryStatus.COMPLETE:
            return self._forward_failure(plan, current_result, "CURRENT_BREAKDOWN_NOT_COMPLETE")
        comparison_result = self.executor.execute(self._query_plan(plan.comparison_spec, plan.question))
        if comparison_result.status is not SemanticQueryStatus.COMPLETE:
            return self._forward_failure(
                plan, comparison_result, "COMPARISON_BREAKDOWN_NOT_COMPLETE", current_result=current_result
            )
        member_limit = int(self.policy["limits"]["max_breakdown_members"])
        if len(current_result.rows) >= member_limit or len(comparison_result.rows) >= member_limit:
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                warnings=[
                    f"Breakdown result reached the governed member cap ({member_limit}); completeness cannot be proven, so ranking/contribution is refused."
                ],
                validation="BREAKDOWN_MEMBER_LIMIT_REACHED",
            )

        if current_result.evidence != "RUNTIME_VERIFIED" or comparison_result.evidence != "RUNTIME_VERIFIED":
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                warnings=["Breakdown derivation requires both grouped windows to carry RUNTIME_VERIFIED evidence."],
                validation="RUNTIME_EVIDENCE_REQUIRED",
            )

        dimension_column = self._dimension_column(plan.dimension, current_result.columns, comparison_result.columns)
        if not dimension_column:
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                warnings=[f"MetricFlow result does not expose the governed breakdown dimension {plan.dimension}."],
                validation="MISSING_BREAKDOWN_DIMENSION_COLUMN",
            )
        current_map, duplicate = self._row_map(current_result.rows, dimension_column)
        if duplicate:
            return self._duplicate_error(plan, current_result, comparison_result, duplicate)
        comparison_map, duplicate = self._row_map(comparison_result.rows, dimension_column)
        if duplicate:
            return self._duplicate_error(plan, current_result, comparison_result, duplicate)

        rows, warnings = self._derive_rows(plan, current_map, comparison_map)
        aggregate_current = aggregate_comparison = None
        if plan.mode is BreakdownAnalysisMode.CONTRIBUTION:
            contribution_result = self._apply_contribution(plan, rows, current_result, comparison_result)
            if isinstance(contribution_result, ComparativeBreakdownResult):
                return contribution_result
            rows, contribution_warnings, aggregate_current, aggregate_comparison = contribution_result
            warnings.extend(contribution_warnings)
        elif plan.mode in {BreakdownAnalysisMode.TOP_ABSOLUTE_CHANGE, BreakdownAnalysisMode.TOP_GROWTH_RATE}:
            rows = self._rank(rows, plan.mode)

        return ComparativeBreakdownResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            rows=rows,
            current_result=current_result,
            comparison_result=comparison_result,
            aggregate_current_result=aggregate_current,
            aggregate_comparison_result=aggregate_comparison,
            warnings=[*plan.warnings, *warnings],
            validation=(
                "GROUPED_WINDOWS_RUNTIME_VERIFIED_AND_CONTRIBUTION_RECONCILED"
                if plan.mode is BreakdownAnalysisMode.CONTRIBUTION
                else "GROUPED_WINDOWS_RUNTIME_VERIFIED_AND_DERIVED"
            ),
        )

    def _derive_rows(self, plan, current_map, comparison_map):
        """按维度 key 对齐两侧结果，生成每个成员的 current/reference/absolute change。"""
        assert plan.current_spec and plan.dimension
        rows: list[ComparativeBreakdownRow] = []
        warnings: list[str] = []
        members = sorted(set(current_map) | set(comparison_map))
        for member in members:
            current_row = current_map.get(member)
            comparison_row = comparison_map.get(member)
            for metric in plan.current_spec.metric_names:
                additive = self.metric_semantics.is_additive(metric)
                current = self._metric_value(current_row, metric)
                comparison = self._metric_value(comparison_row, metric)
                if current is None and current_row is None and additive:
                    current = Decimal("0")
                if comparison is None and comparison_row is None and additive:
                    comparison = Decimal("0")
                if current is None or comparison is None:
                    warnings.append(
                        f"{metric} for {plan.dimension}={member} is missing in one comparison window and is non-additive/undefined; change was not inferred."
                    )
                    rows.append(
                        ComparativeBreakdownRow(plan.dimension, member, metric, self._text_or_none(current), self._text_or_none(comparison), None, None)
                    )
                    continue
                change = current - comparison
                growth = None if comparison == 0 else (change / comparison) * Decimal("100")
                if comparison == 0:
                    warnings.append(f"Growth rate for {metric} at {plan.dimension}={member} is undefined because comparison value is zero.")
                rows.append(
                    ComparativeBreakdownRow(
                        dimension=plan.dimension,
                        dimension_value=member,
                        metric=metric,
                        current_value=self._decimal_text(current),
                        comparison_value=self._decimal_text(comparison),
                        absolute_change=self._decimal_text(change),
                        growth_rate_percent=self._decimal_text(growth, places=4) if growth is not None else None,
                    )
                )
        return rows, list(dict.fromkeys(warnings))

    def _apply_contribution(self, plan, rows, current_result, comparison_result):
        """仅对 additive Metric 用 aggregate change 计算成员贡献率；非 additive Metric 明确不计算。"""
        assert plan.current_spec and plan.comparison_spec
        metric = plan.current_spec.metric_names[0]
        aggregate_current = self.executor.execute(
            self._query_plan(replace(plan.current_spec, group_by=(), limit=1), plan.question)
        )
        if aggregate_current.status is not SemanticQueryStatus.COMPLETE:
            return self._forward_failure(
                plan, aggregate_current, "AGGREGATE_CURRENT_NOT_COMPLETE", current_result=current_result, comparison_result=comparison_result
            )
        aggregate_comparison = self.executor.execute(
            self._query_plan(replace(plan.comparison_spec, group_by=(), limit=1), plan.question)
        )
        if aggregate_comparison.status is not SemanticQueryStatus.COMPLETE:
            return self._forward_failure(
                plan, aggregate_comparison, "AGGREGATE_COMPARISON_NOT_COMPLETE", current_result=current_result, comparison_result=comparison_result,
                aggregate_current=aggregate_current,
            )
        if aggregate_current.evidence != "RUNTIME_VERIFIED" or aggregate_comparison.evidence != "RUNTIME_VERIFIED":
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                aggregate_current_result=aggregate_current,
                aggregate_comparison_result=aggregate_comparison,
                warnings=["Contribution reconciliation requires aggregate queries with RUNTIME_VERIFIED evidence."],
                validation="AGGREGATE_RUNTIME_EVIDENCE_REQUIRED",
            )
        if len(aggregate_current.rows) != 1 or len(aggregate_comparison.rows) != 1:
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                aggregate_current_result=aggregate_current,
                aggregate_comparison_result=aggregate_comparison,
                warnings=["Contribution aggregate reconciliation requires exactly one row per window."],
                validation="AGGREGATE_ROW_SHAPE_INVALID",
            )
        try:
            aggregate_change = Decimal(str(aggregate_current.rows[0][metric])) - Decimal(str(aggregate_comparison.rows[0][metric]))
            grouped_change = sum(
                (Decimal(row.absolute_change) for row in rows if row.metric == metric and row.absolute_change is not None),
                Decimal("0"),
            )
        except (KeyError, InvalidOperation) as exc:
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                aggregate_current_result=aggregate_current,
                aggregate_comparison_result=aggregate_comparison,
                warnings=[f"Contribution reconciliation metric is missing or non-numeric: {exc}"],
                validation="AGGREGATE_VALUE_INVALID",
            )
        tolerance = Decimal(str(self.policy["limits"]["reconciliation_tolerance"]))
        if abs(grouped_change - aggregate_change) > tolerance:
            return ComparativeBreakdownResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                aggregate_current_result=aggregate_current,
                aggregate_comparison_result=aggregate_comparison,
                warnings=[
                    f"Grouped change {self._decimal_text(grouped_change)} does not reconcile to aggregate change {self._decimal_text(aggregate_change)} within tolerance {tolerance}."
                ],
                validation="CONTRIBUTION_RECONCILIATION_FAILED",
            )
        warnings = []
        enriched = []
        if aggregate_change == 0:
            warnings.append("Contribution percentages are undefined because aggregate change is zero.")
        for row in rows:
            contribution = None
            if row.absolute_change is not None and aggregate_change != 0:
                contribution = self._decimal_text((Decimal(row.absolute_change) / aggregate_change) * Decimal("100"), places=4)
            enriched.append(replace(row, contribution_percent=contribution))
        enriched.sort(
            key=lambda item: Decimal(item.absolute_change or "-Infinity"), reverse=True
        )
        enriched = [replace(item, rank=index) for index, item in enumerate(enriched, 1)]
        return enriched, warnings, aggregate_current, aggregate_comparison

    def _rank(self, rows, mode):
        """按绝对变化或贡献度稳定排序并应用结果上限。"""
        if mode is BreakdownAnalysisMode.TOP_GROWTH_RATE:
            eligible = [row for row in rows if row.growth_rate_percent is not None]
            eligible.sort(key=lambda row: Decimal(row.growth_rate_percent), reverse=True)
        else:
            eligible = [row for row in rows if row.absolute_change is not None]
            eligible.sort(key=lambda row: Decimal(row.absolute_change), reverse=True)
        limit = int(self.policy["limits"]["max_top_members"])
        return [replace(row, rank=index) for index, row in enumerate(eligible[:limit], 1)]

    @staticmethod
    def _query_plan(spec: SemanticQuerySpec, question: str) -> SemanticQueryPlan:
        """从基础 SemanticQuerySpec 派生单个窗口的 grouped query，继续走既有 MetricFlow Planner/Executor 边界。"""
        return SemanticQueryPlan(status=SemanticQueryStatus.READY, question=question, spec=spec)

    def _forward_failure(
        self,
        plan,
        failed,
        validation,
        *,
        current_result=None,
        comparison_result=None,
        aggregate_current=None,
    ):
        """把底层查询失败原样投影到 BreakdownResult，避免在失败数据上继续计算。"""
        return ComparativeBreakdownResult(
            status=failed.status,
            evidence=failed.evidence,
            plan=plan,
            current_result=current_result or (failed if validation.startswith("CURRENT") else None),
            comparison_result=comparison_result,
            aggregate_current_result=aggregate_current,
            warnings=[*plan.warnings, *failed.warnings],
            validation=validation,
        )

    def _duplicate_error(self, plan, current_result, comparison_result, member):
        """检测同一维度值出现重复行，防止错误 Grain 被静默聚合。"""
        return ComparativeBreakdownResult(
            status=SemanticQueryStatus.ERROR,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            current_result=current_result,
            comparison_result=comparison_result,
            warnings=[f"Breakdown dimension value {member!r} appeared more than once in one MetricFlow result window."],
            validation="DUPLICATE_BREAKDOWN_MEMBER",
        )

    @staticmethod
    def _row_map(rows, dimension_column):
        """把执行结果行映射成 dimension→row，供 current/reference 对齐。"""
        result = {}
        for row in rows:
            key = str(row.get(dimension_column, "")).strip()
            if not key:
                key = "(null)"
            if key in result:
                return result, key
            result[key] = row
        return result, None

    @staticmethod
    def _dimension_column(dimension, current_columns, comparison_columns):
        """确定 MetricFlow 返回结果中对应的受治理维度列名。"""
        candidates = [dimension, dimension.split("__")[-1]]
        for candidate in candidates:
            if candidate in current_columns and candidate in comparison_columns:
                return candidate
        return None

    @staticmethod
    def _metric_value(row, metric):
        """从结果行中读取指定 Metric 数值并转换为 Decimal。"""
        if row is None:
            return None
        value = row.get(metric)
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    @classmethod
    def _text_or_none(cls, value):
        """把可选值规范化为字符串或 None，避免空值比较漂移。"""
        return cls._decimal_text(value) if value is not None else None

    @staticmethod
    def _date(value: str) -> date:
        """解析查询窗口日期。"""
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()

    @staticmethod
    def _shift_year(value: date, years: int) -> date:
        """按年移动参考窗口日期并处理闰日。"""
        try:
            return value.replace(year=value.year + years)
        except ValueError:
            return value.replace(year=value.year + years, day=28)

    @staticmethod
    def _decimal_text(value: Decimal, places: int | None = None) -> str:
        """把 Decimal 转成稳定文本用于结果契约。"""
        if places is not None:
            quantum = Decimal(1).scaleb(-places)
            value = value.quantize(quantum)
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _plan_stop(status, question, mode, context, warning, *, dimension=None):
        """构造计划阶段的 BLOCKED / CLARIFICATION_REQUIRED 结果并保留原因。"""
        return ComparativeBreakdownPlan(
            status=status,
            question=question,
            mode=mode,
            context=context,
            dimension=dimension,
            warnings=[warning],
        )
