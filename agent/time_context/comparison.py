"""在既有受治理语义查询状态上做时间窗口比较。

业务逻辑：派生 current/reference 两个 bounded MetricFlow plan，再基于两侧结果计算 change。
工程边界：只有两侧都 RUNTIME_VERIFIED 才允许产生派生数值变化；否则保持 DEFERRED/ERROR。
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from agent.semantic_query import (
    MetricFlowSemanticQueryExecutor,
    SemanticQueryPlan,
    SemanticQuerySpec,
    SemanticQueryStatus,
)
from agent.time_context.contracts import (
    ComparativeMetricRow,
    ComparativeQueryPlan,
    ComparativeQueryResult,
    ComparisonMode,
    TimeComparisonContext,
)


class GovernedTimeComparator:
    """构建并执行受治理时间比较，不创建第二套 SQL/指标公式。"""
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/time_comparison_policy.yml").read_text(encoding="utf-8")
        )
        self.executor = MetricFlowSemanticQueryExecutor(self.root)

    def plan(
        self,
        primary_spec: SemanticQuerySpec,
        *,
        context: TimeComparisonContext,
        question: str,
    ) -> ComparativeQueryPlan:
        """从当前 SemanticQuerySpec 派生 comparison window；超出支持的 group-by/时间规则时要求澄清。"""
        try:
            start = self._date(primary_spec.start_time)
            end = self._date(primary_spec.end_time)
        except ValueError as exc:
            return ComparativeQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                context=context,
                warnings=[f"Invalid governed primary time window: {exc}"],
            )
        if end < start:
            return ComparativeQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                context=context,
                warnings=["Primary semantic-query end date is earlier than start date."],
            )

        span_days = (end - start).days + 1
        max_days = int(self.policy["limits"]["max_comparison_window_days"])
        if span_days > max_days:
            return ComparativeQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                context=context,
                warnings=[f"Comparison window is {span_days} days; contract maximum is {max_days}."],
            )
        if context.requested_days is not None and context.requested_days != span_days:
            return ComparativeQueryPlan(
                status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question=question,
                context=context,
                warnings=[
                    f"Primary window is {span_days} days but follow-up requested comparison with {context.requested_days} days. "
                    "Phase 5G requires equal-length comparison windows."
                ],
            )

        non_time_group_by = tuple(g for g in primary_spec.group_by if not g.startswith("metric_time__"))
        if non_time_group_by:
            return ComparativeQueryPlan(
                status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question=question,
                context=context,
                warnings=[
                    "Phase 5G v1 computes an aggregate period comparison. Remove non-time group-by before requesting comparison: "
                    + ", ".join(non_time_group_by)
                ],
            )

        if context.mode is ComparisonMode.PREVIOUS_PERIOD:
            comparison_end = start - timedelta(days=1)
            comparison_start = comparison_end - timedelta(days=span_days - 1)
        elif context.mode is ComparisonMode.YEAR_OVER_YEAR:
            comparison_start = self._shift_year(start, -1)
            comparison_end = self._shift_year(end, -1)
        else:  # defensive for future enum extension
            return ComparativeQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                context=context,
                warnings=[f"Unsupported comparison mode: {context.mode.value}"],
            )

        # Comparison is intentionally aggregate-over-window. The session's original temporal
        # display grain remains frozen in primary_spec; it is not silently changed.
        current = replace(primary_spec, group_by=())
        comparison = replace(
            primary_spec,
            start_time=f"{comparison_start.isoformat()}T00:00:00Z",
            end_time=f"{comparison_end.isoformat()}T23:59:59Z",
            group_by=(),
        )
        warnings: list[str] = []
        if any(g.startswith("metric_time__") for g in primary_spec.group_by):
            warnings.append(
                "Comparison summary aggregates each full window; the session's temporal display grain is preserved but not used for derived growth."
            )
        return ComparativeQueryPlan(
            status=SemanticQueryStatus.READY,
            question=question,
            context=context,
            current_spec=current,
            comparison_spec=comparison,
            warnings=warnings,
        )

    def execute(self, plan: ComparativeQueryPlan) -> ComparativeQueryResult:
        """依次执行 current/reference 两个 MetricFlow plan，并仅在双方 Runtime Verified 时计算 absolute/relative change。"""
        if plan.status is not SemanticQueryStatus.READY or not plan.current_spec or not plan.comparison_spec:
            return ComparativeQueryResult(
                status=plan.status,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=list(plan.warnings),
                validation="NOT_EXECUTED",
            )

        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            return ComparativeQueryResult(
                status=SemanticQueryStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=[
                    *plan.warnings,
                    f"Comparative execution is disabled; set {gate}=true only in the intended runtime environment.",
                ],
                validation="NOT_EXECUTED",
            )

        current_plan = SemanticQueryPlan(
            status=SemanticQueryStatus.READY,
            question=plan.question,
            spec=plan.current_spec,
        )
        comparison_plan = SemanticQueryPlan(
            status=SemanticQueryStatus.READY,
            question=plan.question,
            spec=plan.comparison_spec,
        )
        current_result = self.executor.execute(current_plan)
        if current_result.status is not SemanticQueryStatus.COMPLETE:
            return ComparativeQueryResult(
                status=current_result.status,
                evidence=current_result.evidence,
                plan=plan,
                current_result=current_result,
                warnings=[*plan.warnings, *current_result.warnings],
                validation="CURRENT_WINDOW_NOT_COMPLETE",
            )
        comparison_result = self.executor.execute(comparison_plan)
        if comparison_result.status is not SemanticQueryStatus.COMPLETE:
            return ComparativeQueryResult(
                status=comparison_result.status,
                evidence=comparison_result.evidence,
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                warnings=[*plan.warnings, *comparison_result.warnings],
                validation="COMPARISON_WINDOW_NOT_COMPLETE",
            )

        if current_result.evidence != "RUNTIME_VERIFIED" or comparison_result.evidence != "RUNTIME_VERIFIED":
            return ComparativeQueryResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                warnings=["Derived comparison requires both windows to carry RUNTIME_VERIFIED evidence."],
                validation="RUNTIME_EVIDENCE_REQUIRED",
            )
        if len(current_result.rows) != 1 or len(comparison_result.rows) != 1:
            return ComparativeQueryResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                comparison_result=comparison_result,
                warnings=["Aggregate comparison requires exactly one result row for each time window."],
                validation="COMPARISON_ROW_SHAPE_INVALID",
            )

        rows: list[ComparativeMetricRow] = []
        warnings = list(plan.warnings)
        current_row = current_result.rows[0]
        comparison_row = comparison_result.rows[0]
        for metric in plan.current_spec.metric_names:
            try:
                current_value = Decimal(str(current_row[metric]))
                comparison_value = Decimal(str(comparison_row[metric]))
            except (KeyError, InvalidOperation) as exc:
                return ComparativeQueryResult(
                    status=SemanticQueryStatus.ERROR,
                    evidence="RUNTIME_VERIFIED",
                    plan=plan,
                    current_result=current_result,
                    comparison_result=comparison_result,
                    warnings=[f"Metric {metric} is missing or non-numeric in comparative runtime result: {exc}"],
                    validation="COMPARISON_VALUE_INVALID",
                )
            change = current_value - comparison_value
            if comparison_value == 0:
                growth = None
                warnings.append(f"Growth rate for {metric} is undefined because comparison value is zero.")
            else:
                growth = self._decimal_text((change / comparison_value) * Decimal("100"), places=4)
            rows.append(
                ComparativeMetricRow(
                    metric=metric,
                    current_value=self._decimal_text(current_value),
                    comparison_value=self._decimal_text(comparison_value),
                    absolute_change=self._decimal_text(change),
                    growth_rate_percent=growth,
                )
            )

        return ComparativeQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            rows=rows,
            current_result=current_result,
            comparison_result=comparison_result,
            warnings=warnings,
            validation="BOTH_WINDOWS_RUNTIME_VERIFIED_AND_DERIVED",
        )

    @staticmethod
    def _date(value: str) -> date:
        """把 ISO timestamp 解析为 date，供比较窗口计算使用。"""
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()

    @staticmethod
    def _shift_year(value: date, years: int) -> date:
        """按年移动日期并处理闰日边界，用于 YoY 等受支持比较。"""
        try:
            return value.replace(year=value.year + years)
        except ValueError:
            # Feb 29 -> Feb 28 in a non-leap comparison year.
            return value.replace(year=value.year + years, day=28)

    @staticmethod
    def _decimal_text(value: Decimal, places: int | None = None) -> str:
        """把 Decimal 规范化为稳定字符串，避免浮点格式漂移影响证据比较。"""
        if places is not None:
            quantum = Decimal(1).scaleb(-places)
            value = value.quantize(quantum)
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
