"""建立在 MetricFlow 查询证据上的受治理异常检测。

Phase 6A 使用简单、可解释的 baseline/threshold 规则；异常只是“统计偏离”，不是根因。
工程边界：没有 RUNTIME_VERIFIED 数值证据时不能升级为真实异常观测。
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median

import yaml

from agent.anomaly_analysis.contracts import (
    AnomalyBaselineWindow,
    AnomalyDetectionPlan,
    AnomalyDetectionResult,
    AnomalyDirection,
    AnomalyState,
    DriverAnalysisPlan,
    OperationalHealthSnapshot,
    OperationalHealthState,
    SignalCauseClass,
)
from agent.semantic_query import (
    MetricFlowSemanticQueryExecutor,
    SemanticQueryPlan,
    SemanticQuerySpec,
    SemanticQueryStatus,
)


class GovernedAnomalyDetector:
    """对一个受治理 Metric 的 current window 与历史 baseline 做可解释异常判定。"""
    def __init__(self, project_root: Path | str, *, executor=None):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/anomaly_detection_policy.yml").read_text(encoding="utf-8")
        )
        self.semantic_policy = yaml.safe_load(
            (self.root / "agent/contracts/semantic_query_policy.yml").read_text(encoding="utf-8")
        )
        self.metric_registry = yaml.safe_load(
            (self.root / "metadata/datahub/governance/metric_registry.yml").read_text(encoding="utf-8")
        )
        self.executor = executor or MetricFlowSemanticQueryExecutor(self.root)
        self.governed_metrics = {item["id"] for item in self.metric_registry.get("metrics", [])}

    def plan(self, spec: SemanticQuerySpec, *, question: str = "") -> AnomalyDetectionPlan:
        """构建 current 与 baseline 查询窗口，并验证 Metric、日期和 Runtime permission。"""
        metrics = spec.metric_names
        if len(metrics) != 1:
            return self._stop(
                SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question,
                "Anomaly detection requires exactly one governed metric.",
            )
        metric = metrics[0]
        if metric not in self.governed_metrics:
            return self._stop(
                SemanticQueryStatus.BLOCKED,
                question,
                f"Metric is not in the governed metric registry: {metric}",
                metric=metric,
            )

        start = self._parse(spec.start_time)
        end = self._parse(spec.end_time)
        if end < start:
            return self._stop(
                SemanticQueryStatus.BLOCKED, question, "Anomaly window end precedes start.", metric=metric
            )
        days = (end.date() - start.date()).days + 1
        if days > int(self.policy["limits"]["max_window_days"]):
            return self._stop(
                SemanticQueryStatus.BLOCKED,
                question,
                f"Anomaly window spans {days} days; contract maximum is {self.policy['limits']['max_window_days']}.",
                metric=metric,
            )

        periods = int(self.policy["baseline"]["periods"])
        if self.policy["baseline"].get("require_odd_period_count") and periods % 2 == 0:
            return self._stop(
                SemanticQueryStatus.ERROR,
                question,
                "Anomaly baseline period count must be odd so the median maps to an observed reference window.",
                metric=metric,
            )

        aggregate_spec = replace(spec, metric=metric, metrics=(), group_by=(), limit=1)
        baseline_windows = []
        current_start_date = start.date()
        for index in range(1, periods + 1):
            baseline_end_date = current_start_date - timedelta(days=(index - 1) * days + 1)
            baseline_start_date = baseline_end_date - timedelta(days=days - 1)
            baseline_spec = replace(
                aggregate_spec,
                start_time=f"{baseline_start_date.isoformat()}T00:00:00Z",
                end_time=f"{baseline_end_date.isoformat()}T23:59:59Z",
            )
            baseline_windows.append(AnomalyBaselineWindow(index=index, spec=baseline_spec))

        return AnomalyDetectionPlan(
            status=SemanticQueryStatus.READY,
            question=question,
            metric=metric,
            current_spec=aggregate_spec,
            baseline_windows=tuple(baseline_windows),
        )

    def detect(
        self,
        plan: AnomalyDetectionPlan,
        *,
        operational_health: OperationalHealthSnapshot | None = None,
    ) -> AnomalyDetectionResult:
        """执行 current/baseline 查询并计算相对变化、严重度与方向；底层证据不足时直接转发失败/DEFERRED。"""
        if plan.status is not SemanticQueryStatus.READY or plan.current_spec is None or plan.metric is None:
            return AnomalyDetectionResult(
                status=plan.status,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=list(plan.warnings),
                validation="PLAN_NOT_READY",
            )

        phase_gate = self.policy["runtime"]["allow_env"]
        if os.getenv(phase_gate, "false").lower() != "true":
            return AnomalyDetectionResult(
                status=SemanticQueryStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                operational_health=operational_health,
                warnings=[
                    f"Anomaly execution is disabled; set {phase_gate}=true only in the intended runtime environment."
                ],
                validation="NOT_EXECUTED",
            )

        current_result = self.executor.execute(self._query_plan(plan.current_spec, plan.question))
        if current_result.status is not SemanticQueryStatus.COMPLETE:
            return self._forward_query_failure(plan, current_result, operational_health, "CURRENT_QUERY_NOT_COMPLETE")
        if current_result.evidence != "RUNTIME_VERIFIED":
            return self._runtime_evidence_required(plan, current_result, (), operational_health)

        baseline_results = []
        for window in plan.baseline_windows:
            result = self.executor.execute(self._query_plan(window.spec, plan.question))
            baseline_results.append(result)
            if result.status is not SemanticQueryStatus.COMPLETE:
                return self._forward_query_failure(
                    plan,
                    result,
                    operational_health,
                    f"BASELINE_QUERY_{window.index}_NOT_COMPLETE",
                    current_result=current_result,
                    baseline_results=tuple(baseline_results),
                )
            if result.evidence != "RUNTIME_VERIFIED":
                return self._runtime_evidence_required(
                    plan, current_result, tuple(baseline_results), operational_health
                )

        try:
            current_value = self._single_metric_value(current_result, plan.metric)
            baseline_values = [self._single_metric_value(item, plan.metric) for item in baseline_results]
        except (KeyError, ValueError, InvalidOperation) as exc:
            return AnomalyDetectionResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                baseline_results=tuple(baseline_results),
                operational_health=operational_health,
                warnings=[f"Anomaly input must contain one numeric aggregate row per window: {exc}"],
                validation="ANOMALY_INPUT_INVALID",
            )

        minimum = int(self.policy["baseline"]["minimum_periods"])
        if len(baseline_values) < minimum:
            return AnomalyDetectionResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                current_result=current_result,
                baseline_results=tuple(baseline_results),
                operational_health=operational_health,
                warnings=[f"Only {len(baseline_values)} baseline periods are available; at least {minimum} are required."],
                validation="INSUFFICIENT_BASELINE_PERIODS",
            )

        baseline_value = Decimal(str(median(baseline_values)))
        absolute_change = current_value - baseline_value
        direction = (
            AnomalyDirection.UP if absolute_change > 0 else
            AnomalyDirection.DOWN if absolute_change < 0 else
            AnomalyDirection.STABLE
        )

        if baseline_value == 0:
            if current_value == 0:
                relative_change = Decimal("0")
                anomaly_state = AnomalyState.NORMAL
            else:
                return AnomalyDetectionResult(
                    status=SemanticQueryStatus.COMPLETE,
                    evidence="RUNTIME_VERIFIED",
                    plan=plan,
                    anomaly_state=AnomalyState.UNRESOLVED,
                    direction=direction,
                    cause_class=SignalCauseClass.UNRESOLVED,
                    current_value=self._decimal_text(current_value),
                    baseline_value="0",
                    absolute_change=self._decimal_text(absolute_change),
                    baseline_values=tuple(self._decimal_text(item) for item in baseline_values),
                    current_result=current_result,
                    baseline_results=tuple(baseline_results),
                    operational_health=operational_health,
                    warnings=["Relative anomaly severity is undefined because the median baseline is zero."],
                    validation="BASELINE_ZERO_RELATIVE_CHANGE_UNDEFINED",
                )
        else:
            relative_change = (absolute_change / abs(baseline_value)) * Decimal("100")
            anomaly_state = self._classify(abs(relative_change))

        reference_index = self._reference_window_index(baseline_values, baseline_value)
        cause_class = self._cause_class(anomaly_state, operational_health)
        driver_plan = self._driver_plan(
            plan,
            anomaly_state=anomaly_state,
            cause_class=cause_class,
            reference_index=reference_index,
        )

        return AnomalyDetectionResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            anomaly_state=anomaly_state,
            direction=direction,
            cause_class=cause_class,
            current_value=self._decimal_text(current_value),
            baseline_value=self._decimal_text(baseline_value),
            absolute_change=self._decimal_text(absolute_change),
            relative_change_percent=self._decimal_text(relative_change, places=4),
            baseline_values=tuple(self._decimal_text(item) for item in baseline_values),
            reference_window_index=reference_index,
            current_result=current_result,
            baseline_results=tuple(baseline_results),
            operational_health=operational_health,
            driver_plan=driver_plan,
            validation="ANOMALY_MEDIAN_BASELINE_COMPLETE",
        )

    def _classify(self, absolute_relative_percent: Decimal) -> AnomalyState:
        """根据政策阈值把相对变化映射为 NORMAL / WARNING / CRITICAL。"""
        threshold = self.policy["thresholds"]["default"]
        critical = Decimal(str(threshold["critical_relative_percent"]))
        warning = Decimal(str(threshold["warning_relative_percent"]))
        if absolute_relative_percent >= critical:
            return AnomalyState.CRITICAL
        if absolute_relative_percent >= warning:
            return AnomalyState.WARNING
        return AnomalyState.NORMAL

    def _cause_class(
        self,
        anomaly_state: AnomalyState,
        operational_health: OperationalHealthSnapshot | None,
    ) -> SignalCauseClass:
        """结合异常证据与 operational health 判断 BUSINESS_SIGNAL_SUSPECTED / DATA_PIPELINE_SUSPECTED / UNKNOWN。"""
        if anomaly_state is AnomalyState.NORMAL:
            return SignalCauseClass.NO_ANOMALY
        if anomaly_state is AnomalyState.UNRESOLVED:
            return SignalCauseClass.UNRESOLVED
        if operational_health is None or operational_health.evidence != "RUNTIME_VERIFIED":
            return SignalCauseClass.UNRESOLVED
        if operational_health.state is OperationalHealthState.UNHEALTHY:
            return SignalCauseClass.DATA_PIPELINE_SUSPECTED
        if operational_health.state is OperationalHealthState.HEALTHY:
            return SignalCauseClass.BUSINESS_SIGNAL_SUSPECTED
        return SignalCauseClass.UNRESOLVED

    def _driver_plan(
        self,
        plan: AnomalyDetectionPlan,
        *,
        anomaly_state: AnomalyState,
        cause_class: SignalCauseClass,
        reference_index: int,
    ) -> DriverAnalysisPlan | None:
        """只有业务信号且健康门满足时才生成后续 Driver Attribution 计划。"""
        if anomaly_state not in {AnomalyState.WARNING, AnomalyState.CRITICAL}:
            return None
        assert plan.current_spec is not None and plan.metric is not None
        reference_spec = plan.baseline_windows[reference_index - 1].spec
        if cause_class is not SignalCauseClass.BUSINESS_SIGNAL_SUSPECTED:
            reason = (
                "Operational runtime is unhealthy; business-driver attribution is blocked."
                if cause_class is SignalCauseClass.DATA_PIPELINE_SUSPECTED
                else "Operational runtime health is not RUNTIME_VERIFIED; business-driver attribution is blocked."
            )
            return DriverAnalysisPlan(
                status=SemanticQueryStatus.BLOCKED,
                metric=plan.metric,
                current_spec=plan.current_spec,
                reference_spec=reference_spec,
                warnings=[reason],
            )

        allowlist = set(self.semantic_policy.get("structured_filter_dimensions", {}))
        priority = [item for item in self.policy.get("driver_dimension_priority", []) if item in allowlist]
        dimensions = tuple(priority[: int(self.policy["limits"]["max_driver_dimensions"])])
        return DriverAnalysisPlan(
            status=SemanticQueryStatus.READY,
            metric=plan.metric,
            current_spec=plan.current_spec,
            reference_spec=reference_spec,
            dimensions=dimensions,
            warnings=[
                "Driver dimensions are candidates only; each must still pass MetricFlow Explain before any attribution query."
            ],
        )

    @staticmethod
    def _single_metric_value(result, metric: str) -> Decimal:
        """从 MetricFlow 结果中读取唯一聚合值；多行/缺值时拒绝假设。"""
        if len(result.rows) != 1:
            raise ValueError(f"expected exactly one aggregate row, got {len(result.rows)}")
        if metric not in result.rows[0]:
            raise KeyError(metric)
        return Decimal(str(result.rows[0][metric]))

    @staticmethod
    def _reference_window_index(values: list[Decimal], baseline: Decimal) -> int:
        """计算 baseline 中各参考窗口位置，保证 current/reference 对齐。"""
        for index, value in enumerate(values, 1):
            if value == baseline:
                return index
        raise ValueError("median baseline does not map to an observed baseline window")

    @staticmethod
    def _parse(value: str) -> datetime:
        """解析输入日期或时间文本。"""
        text = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _query_plan(spec: SemanticQuerySpec, question: str) -> SemanticQueryPlan:
        """为 current/baseline 单个窗口构造受治理 SemanticQueryPlan。"""
        return SemanticQueryPlan(status=SemanticQueryStatus.READY, question=question, spec=spec)

    @staticmethod
    def _decimal_text(value: Decimal, places: int | None = None) -> str:
        """规范化 Decimal 输出。"""
        if places is not None:
            value = value.quantize(Decimal("1." + "0" * places))
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    def _stop(self, status, question, warning, *, metric=None):
        """构造异常检测阶段的安全停止结果并保留原因。"""
        return AnomalyDetectionPlan(
            status=status,
            question=question,
            metric=metric,
            warnings=[warning],
        )

    @staticmethod
    def _forward_query_failure(
        plan,
        failed,
        operational_health,
        validation,
        *,
        current_result=None,
        baseline_results=(),
    ):
        """把底层语义查询失败直接映射到 AnomalyResult，不在失败证据上计算异常。"""
        return AnomalyDetectionResult(
            status=failed.status,
            evidence=failed.evidence,
            plan=plan,
            current_result=current_result or failed,
            baseline_results=baseline_results,
            operational_health=operational_health,
            warnings=list(failed.warnings),
            validation=validation,
        )

    @staticmethod
    def _runtime_evidence_required(plan, current_result, baseline_results, operational_health):
        """确认数值结果具有 RUNTIME_VERIFIED evidence；静态/fake 结果不能冒充真实异常。"""
        return AnomalyDetectionResult(
            status=SemanticQueryStatus.BLOCKED,
            evidence="STATIC_CONTRACT",
            plan=plan,
            current_result=current_result,
            baseline_results=baseline_results,
            operational_health=operational_health,
            warnings=["Anomaly classification requires RUNTIME_VERIFIED current and baseline MetricFlow evidence."],
            validation="RUNTIME_EVIDENCE_REQUIRED",
        )
