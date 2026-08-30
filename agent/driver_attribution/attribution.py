"""在 RUNTIME_VERIFIED 异常之上的受治理 Driver Attribution。

每个 Region / Brand / Category 都是独立 analytical lens；通过 MetricFlow grouped query 比较 current/reference。
工程边界：结果是驱动线索而非因果证明，不允许跨 lens 相加贡献率。
"""
from __future__ import annotations

import os
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from agent.anomaly_analysis import (
    AnomalyDetectionResult,
    AnomalyDirection,
    AnomalyState,
    OperationalHealthState,
    SignalCauseClass,
)
from agent.breakdown_analysis import MetricContributionSemantics
from agent.semantic_query import MetricFlowSemanticQueryExecutor, SemanticQueryPlan, SemanticQueryStatus
from agent.driver_attribution.contracts import (
    DriverAttributionPlan,
    DriverAttributionResult,
    DriverAttributionRow,
    DriverAttributionStatus,
    DriverLensPlan,
    DriverLensResult,
)


class GovernedDriverAttribution:
    """对满足健康门的异常执行多个独立维度 lens，输出 strongest driver 与贡献证据。"""
    def __init__(self, project_root: Path | str, *, executor=None):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/driver_attribution_policy.yml").read_text(encoding="utf-8")
        )
        self.executor = executor or MetricFlowSemanticQueryExecutor(self.root)
        self.metric_semantics = MetricContributionSemantics(self.root)

    def plan(self, anomaly: AnomalyDetectionResult) -> DriverAttributionPlan:
        """从 anomaly result 构造允许的 driver lens 计划；输入不满足 RUNTIME_VERIFIED/健康条件时 Fail Closed。"""
        warning = self._input_guard(anomaly)
        if warning:
            return DriverAttributionPlan(
                status=DriverAttributionStatus.BLOCKED,
                metric=anomaly.plan.metric,
                direction=anomaly.direction,
                warnings=[warning],
            )
        assert anomaly.driver_plan is not None
        assert anomaly.driver_plan.current_spec is not None
        assert anomaly.driver_plan.reference_spec is not None
        metric = anomaly.driver_plan.metric
        additive = self.metric_semantics.is_additive(metric)
        max_lenses = int(self.policy["limits"]["max_driver_dimensions"])
        member_limit = int(self.policy["limits"]["max_members_per_dimension"])
        dimensions = anomaly.driver_plan.dimensions[:max_lenses]
        lenses = tuple(
            DriverLensPlan(
                dimension=dimension,
                current_spec=replace(
                    anomaly.driver_plan.current_spec,
                    group_by=(dimension,),
                    limit=member_limit,
                ),
                reference_spec=replace(
                    anomaly.driver_plan.reference_spec,
                    group_by=(dimension,),
                    limit=member_limit,
                ),
                additive=additive,
            )
            for dimension in dimensions
        )
        if not lenses:
            return DriverAttributionPlan(
                status=DriverAttributionStatus.BLOCKED,
                metric=metric,
                direction=anomaly.direction,
                warnings=["No governed driver dimensions remain after Phase 6A candidate filtering."],
            )
        return DriverAttributionPlan(
            status=DriverAttributionStatus.READY,
            metric=metric,
            direction=anomaly.direction,
            lenses=lenses,
            warnings=[
                "Each driver dimension is an independent analytical lens; contribution percentages must never be summed across lenses."
            ],
        )

    def execute(self, anomaly: AnomalyDetectionResult, plan: DriverAttributionPlan | None = None) -> DriverAttributionResult:
        """逐个执行 lens，并汇总成功/失败状态；任何 lens 仍沿用 MetricFlow 受治理查询边界。"""
        plan = plan or self.plan(anomaly)
        if plan.status is not DriverAttributionStatus.READY:
            return DriverAttributionResult(
                status=plan.status,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=list(plan.warnings),
                validation="PLAN_NOT_READY",
            )
        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            return DriverAttributionResult(
                status=DriverAttributionStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=[*plan.warnings, f"Driver attribution is disabled; set {gate}=true only in the intended runtime environment."],
                validation="NOT_EXECUTED",
            )

        lenses = [self._execute_lens(anomaly, lens, plan.direction) for lens in plan.lenses]
        complete = [item for item in lenses if item.status is DriverAttributionStatus.COMPLETE]
        failed = [item for item in lenses if item.status is not DriverAttributionStatus.COMPLETE]
        if complete and failed:
            status = DriverAttributionStatus.PARTIAL
            validation = "PARTIAL_DRIVER_LENSES_RUNTIME_VERIFIED"
        elif complete:
            status = DriverAttributionStatus.COMPLETE
            validation = "ALL_DRIVER_LENSES_RUNTIME_VERIFIED"
        else:
            status = DriverAttributionStatus.ERROR
            validation = "NO_DRIVER_LENS_COMPLETED"
        warnings = list(plan.warnings)
        for item in failed:
            warnings.extend(f"{item.dimension}: {warning}" for warning in item.warnings)
        if complete:
            warnings.append(
                "Strongest drivers are reported separately per dimension lens; Region/Brand/Category contribution percentages are not cross-summed."
            )
        return DriverAttributionResult(
            status=status,
            evidence="RUNTIME_VERIFIED" if complete else "STATIC_CONTRACT",
            plan=plan,
            lenses=lenses,
            warnings=list(dict.fromkeys(warnings)),
            validation=validation,
        )

    def _execute_lens(self, anomaly: AnomalyDetectionResult, lens: DriverLensPlan, direction: AnomalyDirection) -> DriverLensResult:
        """执行单个维度 lens 的 current/reference grouped queries，并派生成员变化与排名。"""
        metric = lens.current_spec.metric_names[0]
        current_plan = SemanticQueryPlan(SemanticQueryStatus.READY, f"driver:{lens.dimension}:current", spec=lens.current_spec)
        reference_plan = SemanticQueryPlan(SemanticQueryStatus.READY, f"driver:{lens.dimension}:reference", spec=lens.reference_spec)
        current = self.executor.execute(current_plan)
        if current.status is not SemanticQueryStatus.COMPLETE:
            return self._lens_failure(lens, current, None, "CURRENT_DRIVER_QUERY_NOT_COMPLETE")
        reference = self.executor.execute(reference_plan)
        if reference.status is not SemanticQueryStatus.COMPLETE:
            return self._lens_failure(lens, current, reference, "REFERENCE_DRIVER_QUERY_NOT_COMPLETE")
        if current.evidence != "RUNTIME_VERIFIED" or reference.evidence != "RUNTIME_VERIFIED":
            return DriverLensResult(
                dimension=lens.dimension,
                status=DriverAttributionStatus.BLOCKED,
                evidence="STATIC_CONTRACT",
                additive=lens.additive,
                current_result=current,
                reference_result=reference,
                warnings=["Driver attribution requires both grouped windows to carry RUNTIME_VERIFIED evidence."],
                validation="RUNTIME_EVIDENCE_REQUIRED",
            )
        limit = int(self.policy["limits"]["max_members_per_dimension"])
        if len(current.rows) >= limit or len(reference.rows) >= limit:
            return DriverLensResult(
                dimension=lens.dimension,
                status=DriverAttributionStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                additive=lens.additive,
                current_result=current,
                reference_result=reference,
                warnings=["Driver dimension reached the governed member limit; completeness cannot be proven."],
                validation="DRIVER_MEMBER_LIMIT_REACHED",
            )
        dimension_column = self._dimension_column(lens.dimension, current.columns, reference.columns)
        if not dimension_column:
            return DriverLensResult(
                dimension=lens.dimension,
                status=DriverAttributionStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                additive=lens.additive,
                current_result=current,
                reference_result=reference,
                warnings=[f"Runtime result does not contain a shared column for driver dimension {lens.dimension}."],
                validation="DRIVER_DIMENSION_COLUMN_MISSING",
            )
        current_map, duplicate = self._row_map(current.rows, dimension_column)
        if duplicate:
            return self._duplicate_error(lens, current, reference, duplicate)
        reference_map, duplicate = self._row_map(reference.rows, dimension_column)
        if duplicate:
            return self._duplicate_error(lens, current, reference, duplicate)

        rows: list[DriverAttributionRow] = []
        warnings: list[str] = []
        for member in sorted(set(current_map) | set(reference_map)):
            c = self._metric_value(current_map.get(member), metric)
            r = self._metric_value(reference_map.get(member), metric)
            if lens.additive:
                c = c if c is not None else Decimal("0")
                r = r if r is not None else Decimal("0")
            if c is None or r is None:
                rows.append(
                    DriverAttributionRow(
                        dimension=lens.dimension,
                        dimension_value=member,
                        metric=metric,
                        current_value=self._text_or_none(c),
                        reference_value=self._text_or_none(r),
                        absolute_change=None,
                        growth_rate_percent=None,
                    )
                )
                warnings.append(
                    f"{lens.dimension}={member}: missing one-side value is undefined for non-additive metric {metric}; it was not coerced to zero."
                )
                continue
            change = c - r
            growth = None if r == 0 else (change / r) * Decimal("100")
            rows.append(
                DriverAttributionRow(
                    dimension=lens.dimension,
                    dimension_value=member,
                    metric=metric,
                    current_value=self._decimal_text(c),
                    reference_value=self._decimal_text(r),
                    absolute_change=self._decimal_text(change),
                    growth_rate_percent=self._decimal_text(growth, places=4) if growth is not None else None,
                )
            )

        if lens.additive:
            reconciled = self._reconcile_additive(anomaly, lens, rows, current_map, reference_map, metric)
            if isinstance(reconciled, DriverLensResult):
                reconciled.current_result = current
                reconciled.reference_result = reference
                return reconciled
            rows = reconciled

        rows = self._rank(rows, direction)
        if not rows:
            return DriverLensResult(
                dimension=lens.dimension,
                status=DriverAttributionStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                additive=lens.additive,
                current_result=current,
                reference_result=reference,
                warnings=["No numeric driver rows were available for direction-aware ranking."],
                validation="NO_RANKABLE_DRIVER_ROWS",
            )
        return DriverLensResult(
            dimension=lens.dimension,
            status=DriverAttributionStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            additive=lens.additive,
            rows=rows,
            current_result=current,
            reference_result=reference,
            warnings=list(dict.fromkeys(warnings)),
            validation="DRIVER_LENS_RUNTIME_VERIFIED" + ("_AND_RECONCILED" if lens.additive else "_NON_ADDITIVE"),
        )

    def _reconcile_additive(self, anomaly, lens, rows, current_map, reference_map, metric):
        """对 additive Metric 校验所有成员变化之和与总体变化是否可对账。"""
        try:
            expected_current = Decimal(str(anomaly.current_value))
            expected_reference = Decimal(str(anomaly.baseline_value))
            grouped_current = sum((self._metric_value(row, metric) or Decimal("0") for row in current_map.values()), Decimal("0"))
            grouped_reference = sum((self._metric_value(row, metric) or Decimal("0") for row in reference_map.values()), Decimal("0"))
        except (InvalidOperation, TypeError) as exc:
            return DriverLensResult(
                dimension=lens.dimension,
                status=DriverAttributionStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                additive=True,
                warnings=[f"Anomaly aggregate values are missing/non-numeric for driver reconciliation: {exc}"],
                validation="ANOMALY_AGGREGATE_INVALID",
            )
        tolerance = Decimal(str(self.policy["limits"]["reconciliation_tolerance"]))
        if abs(grouped_current - expected_current) > tolerance or abs(grouped_reference - expected_reference) > tolerance:
            return DriverLensResult(
                dimension=lens.dimension,
                status=DriverAttributionStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                additive=True,
                warnings=[
                    "Grouped driver values do not reconcile to the Phase 6A aggregate current/reference values; contribution attribution is blocked."
                ],
                validation="DRIVER_RECONCILIATION_FAILED",
            )
        total_change = expected_current - expected_reference
        enriched = []
        for row in rows:
            contribution = None
            if row.absolute_change is not None and total_change != 0:
                contribution = self._decimal_text(
                    (Decimal(row.absolute_change) / total_change) * Decimal("100"),
                    places=4,
                )
            enriched.append(replace(row, contribution_percent=contribution))
        return enriched

    def _rank(self, rows: list[DriverAttributionRow], direction: AnomalyDirection) -> list[DriverAttributionRow]:
        """按绝对变化大小稳定排序 driver rows。"""
        eligible = [row for row in rows if row.absolute_change is not None]
        if direction is AnomalyDirection.DOWN:
            eligible.sort(key=lambda row: Decimal(row.absolute_change))
        elif direction is AnomalyDirection.UP:
            eligible.sort(key=lambda row: Decimal(row.absolute_change), reverse=True)
        else:
            return []
        limit = int(self.policy["limits"]["max_ranked_members_per_dimension"])
        return [replace(row, rank=index) for index, row in enumerate(eligible[:limit], 1)]

    def _input_guard(self, anomaly: AnomalyDetectionResult) -> str | None:
        """验证 Phase 6A anomaly、operational health 与 signal cause 是否允许进入归因阶段。"""
        if anomaly.status is not SemanticQueryStatus.COMPLETE or anomaly.evidence != "RUNTIME_VERIFIED":
            return "Driver attribution requires a COMPLETE, RUNTIME_VERIFIED Phase 6A anomaly result."
        if anomaly.anomaly_state not in {AnomalyState.WARNING, AnomalyState.CRITICAL}:
            return "Driver attribution requires a WARNING or CRITICAL anomaly."
        if anomaly.direction not in {AnomalyDirection.UP, AnomalyDirection.DOWN}:
            return "Driver attribution requires a directional UP/DOWN anomaly."
        if anomaly.cause_class is not SignalCauseClass.BUSINESS_SIGNAL_SUSPECTED:
            return "Driver attribution is allowed only when Phase 6A classifies the signal as BUSINESS_SIGNAL_SUSPECTED."
        if anomaly.operational_health is None or anomaly.operational_health.evidence != "RUNTIME_VERIFIED":
            return "Driver attribution requires RUNTIME_VERIFIED operational-health evidence."
        if anomaly.operational_health.state is not OperationalHealthState.HEALTHY:
            return "Driver attribution is blocked while the data pipeline is not verified healthy."
        if anomaly.driver_plan is None or anomaly.driver_plan.status is not SemanticQueryStatus.READY:
            return "Phase 6A did not produce a READY driver-analysis plan."
        if anomaly.driver_plan.reference_spec is None:
            return "Phase 6A did not preserve the real median-reference window."
        return None

    @staticmethod
    def _lens_failure(lens, current, reference, validation):
        """把单个 lens 的查询或对账失败转换成受控 LensResult。"""
        failed = reference or current
        mapping = {
            SemanticQueryStatus.DEFERRED: DriverAttributionStatus.DEFERRED,
            SemanticQueryStatus.BLOCKED: DriverAttributionStatus.BLOCKED,
            SemanticQueryStatus.ERROR: DriverAttributionStatus.ERROR,
            SemanticQueryStatus.CLARIFICATION_REQUIRED: DriverAttributionStatus.BLOCKED,
            SemanticQueryStatus.READY: DriverAttributionStatus.ERROR,
            SemanticQueryStatus.COMPLETE: DriverAttributionStatus.COMPLETE,
        }
        return DriverLensResult(
            dimension=lens.dimension,
            status=mapping[failed.status],
            evidence=failed.evidence,
            additive=lens.additive,
            current_result=current,
            reference_result=reference,
            warnings=list(failed.warnings),
            validation=validation,
        )

    @staticmethod
    def _row_map(rows, dimension_column):
        """按维度值建立查询结果映射，供 current/reference 对齐。"""
        result = {}
        for row in rows:
            key = str(row.get(dimension_column, "")).strip() or "(null)"
            if key in result:
                return result, key
            result[key] = row
        return result, None

    @staticmethod
    def _dimension_column(dimension, current_columns, reference_columns):
        """确定当前 lens 在 MetricFlow 返回中的维度列。"""
        for candidate in (dimension, dimension.split("__")[-1]):
            if candidate in current_columns and candidate in reference_columns:
                return candidate
        return None

    @staticmethod
    def _metric_value(row, metric):
        """读取并转换指定 Metric 数值。"""
        if row is None or row.get(metric) in (None, ""):
            return None
        try:
            return Decimal(str(row[metric]))
        except InvalidOperation:
            return None

    @classmethod
    def _text_or_none(cls, value):
        """把可选文本规范化。"""
        return cls._decimal_text(value) if value is not None else None

    @staticmethod
    def _decimal_text(value: Decimal, places: int | None = None) -> str:
        """把 Decimal 规范化为稳定文本。"""
        if places is not None:
            quantum = Decimal(1).scaleb(-places)
            value = value.quantize(quantum)
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _duplicate_error(lens, current, reference, member):
        """检测 grouped query 是否出现重复维度 key，避免错误 Grain 被吞掉。"""
        return DriverLensResult(
            dimension=lens.dimension,
            status=DriverAttributionStatus.ERROR,
            evidence="RUNTIME_VERIFIED",
            additive=lens.additive,
            current_result=current,
            reference_result=reference,
            warnings=[f"Driver dimension value {member!r} appeared more than once in one runtime result."],
            validation="DUPLICATE_DRIVER_MEMBER",
        )
