from __future__ import annotations

from pathlib import Path

import pytest

from agent.anomaly_analysis import (
    AnomalyState,
    GovernedAnomalyDetector,
    OperationalHealthSnapshot,
    OperationalHealthState,
    SignalCauseClass,
)
from agent.semantic_query import (
    SemanticQueryPlan,
    SemanticQueryResult,
    SemanticQuerySpec,
    SemanticQueryStatus,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeExecutor:
    def __init__(self, values, *, evidence="RUNTIME_VERIFIED"):
        self.values = list(values)
        self.evidence = evidence
        self.calls = []

    def execute(self, plan: SemanticQueryPlan):
        self.calls.append(plan)
        value = self.values.pop(0)
        metric = plan.spec.metric_names[0]
        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence=self.evidence,
            plan=plan,
            rows=[{metric: str(value)}],
            columns=[metric],
            validation="FAKE_RUNTIME",
        )


def spec(metric="gross_sales", *, start="2026-08-05T00:00:00Z", end="2026-08-05T23:59:59Z", group_by=()):
    return SemanticQuerySpec(metric=metric, start_time=start, end_time=end, group_by=group_by, limit=20)


def health(state):
    return OperationalHealthSnapshot(state=state, evidence="RUNTIME_VERIFIED", details="test")


def test_plan_builds_seven_equal_non_overlapping_previous_windows_and_strips_display_group_by():
    detector = GovernedAnomalyDetector(ROOT)
    plan = detector.plan(spec(group_by=("metric_time__day",)), question="gross_sales 异常吗？")
    assert plan.status is SemanticQueryStatus.READY
    assert plan.current_spec.group_by == ()
    assert plan.current_spec.limit == 1
    assert len(plan.baseline_windows) == 7
    assert plan.baseline_windows[0].spec.start_time == "2026-08-04T00:00:00Z"
    assert plan.baseline_windows[0].spec.end_time == "2026-08-04T23:59:59Z"
    assert plan.baseline_windows[-1].spec.start_time == "2026-07-29T00:00:00Z"


def test_five_day_window_produces_equal_length_baseline_periods():
    detector = GovernedAnomalyDetector(ROOT)
    plan = detector.plan(spec(start="2026-08-01T00:00:00Z", end="2026-08-05T23:59:59Z"))
    first = plan.baseline_windows[0].spec
    second = plan.baseline_windows[1].spec
    assert (first.start_time, first.end_time) == ("2026-07-27T00:00:00Z", "2026-07-31T23:59:59Z")
    assert (second.start_time, second.end_time) == ("2026-07-22T00:00:00Z", "2026-07-26T23:59:59Z")


def test_ungoverned_metric_is_blocked():
    detector = GovernedAnomalyDetector(ROOT)
    plan = detector.plan(spec(metric="units_ordered"))
    assert plan.status is SemanticQueryStatus.BLOCKED


def test_anomaly_detection_is_deferred_before_phase6_runtime_gate(monkeypatch):
    fake = FakeExecutor([100] * 8)
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "false")
    result = detector.detect(plan)
    assert result.status is SemanticQueryStatus.DEFERRED
    assert result.validation == "NOT_EXECUTED"
    assert fake.calls == []


def test_critical_down_anomaly_uses_median_baseline(monkeypatch):
    # current 50; previous values median to 100 -> -50%, CRITICAL
    fake = FakeExecutor([50, 90, 100, 110, 95, 105, 100, 102])
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan, operational_health=health(OperationalHealthState.HEALTHY))
    assert result.status is SemanticQueryStatus.COMPLETE
    assert result.evidence == "RUNTIME_VERIFIED"
    assert result.baseline_value == "100"
    assert result.current_value == "50"
    assert result.relative_change_percent == "-50"
    assert result.anomaly_state is AnomalyState.CRITICAL
    assert result.cause_class is SignalCauseClass.BUSINESS_SIGNAL_SUSPECTED
    assert result.driver_plan.status is SemanticQueryStatus.READY
    assert result.driver_plan.reference_spec is not None
    assert result.driver_plan.dimensions == ("store__region", "item__brand", "item__category")


def test_warning_anomaly_threshold(monkeypatch):
    fake = FakeExecutor([75, 100, 100, 100, 100, 100, 100, 100])
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan, operational_health=health(OperationalHealthState.HEALTHY))
    assert result.anomaly_state is AnomalyState.WARNING
    assert result.relative_change_percent == "-25"


def test_normal_signal_has_no_driver_plan(monkeypatch):
    fake = FakeExecutor([95, 100, 100, 100, 100, 100, 100, 100])
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan, operational_health=health(OperationalHealthState.HEALTHY))
    assert result.anomaly_state is AnomalyState.NORMAL
    assert result.cause_class is SignalCauseClass.NO_ANOMALY
    assert result.driver_plan is None


def test_operational_health_unknown_blocks_business_attribution(monkeypatch):
    fake = FakeExecutor([50, 100, 100, 100, 100, 100, 100, 100])
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan)
    assert result.anomaly_state is AnomalyState.CRITICAL
    assert result.cause_class is SignalCauseClass.UNRESOLVED
    assert result.driver_plan.status is SemanticQueryStatus.BLOCKED
    assert result.driver_plan.dimensions == ()


def test_unhealthy_runtime_marks_data_pipeline_suspected_and_blocks_business_drivers(monkeypatch):
    fake = FakeExecutor([50, 100, 100, 100, 100, 100, 100, 100])
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan, operational_health=health(OperationalHealthState.UNHEALTHY))
    assert result.cause_class is SignalCauseClass.DATA_PIPELINE_SUSPECTED
    assert result.driver_plan.status is SemanticQueryStatus.BLOCKED


def test_static_query_evidence_cannot_certify_anomaly(monkeypatch):
    fake = FakeExecutor([50, 100, 100, 100, 100, 100, 100, 100], evidence="STATIC_CONTRACT")
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan)
    assert result.status is SemanticQueryStatus.BLOCKED
    assert result.validation == "RUNTIME_EVIDENCE_REQUIRED"
    assert result.anomaly_state is AnomalyState.UNRESOLVED


def test_nonzero_current_against_zero_median_is_unresolved_not_infinite_growth(monkeypatch):
    fake = FakeExecutor([10, 0, 0, 0, 0, 0, 0, 0])
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan, operational_health=health(OperationalHealthState.HEALTHY))
    assert result.status is SemanticQueryStatus.COMPLETE
    assert result.anomaly_state is AnomalyState.UNRESOLVED
    assert result.relative_change_percent is None
    assert result.validation == "BASELINE_ZERO_RELATIVE_CHANGE_UNDEFINED"


def test_metric_result_must_be_single_numeric_aggregate_row(monkeypatch):
    class BadExecutor(FakeExecutor):
        def execute(self, plan):
            self.calls.append(plan)
            metric = plan.spec.metric_names[0]
            return SemanticQueryResult(
                status=SemanticQueryStatus.COMPLETE,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                rows=[{metric: "abc"}],
                columns=[metric],
            )
    detector = GovernedAnomalyDetector(ROOT, executor=BadExecutor([]))
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan)
    assert result.status is SemanticQueryStatus.ERROR
    assert result.validation == "ANOMALY_INPUT_INVALID"


def test_driver_dimension_candidates_are_governed_and_bounded(monkeypatch):
    fake = FakeExecutor([150, 100, 100, 100, 100, 100, 100, 100])
    detector = GovernedAnomalyDetector(ROOT, executor=fake)
    plan = detector.plan(spec())
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    result = detector.detect(plan, operational_health=health(OperationalHealthState.HEALTHY))
    allowed = set(detector.semantic_policy["structured_filter_dimensions"])
    assert set(result.driver_plan.dimensions).issubset(allowed)
    assert len(result.driver_plan.dimensions) <= detector.policy["limits"]["max_driver_dimensions"]


def test_long_anomaly_window_is_blocked():
    detector = GovernedAnomalyDetector(ROOT)
    plan = detector.plan(spec(start="2026-01-01T00:00:00Z", end="2026-03-01T23:59:59Z"))
    assert plan.status is SemanticQueryStatus.BLOCKED
