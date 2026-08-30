from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent.anomaly_analysis import (
    GovernedAnomalyDetector,
    OperationalHealthSnapshot,
    OperationalHealthState,
)
from agent.driver_attribution import DriverAttributionStatus, GovernedDriverAttribution
from agent.semantic_query import SemanticQueryPlan, SemanticQueryResult, SemanticQuerySpec, SemanticQueryStatus


ROOT = Path(__file__).resolve().parents[1]


class SequenceExecutor:
    def __init__(self, values, evidence="RUNTIME_VERIFIED"):
        self.values = list(values)
        self.evidence = evidence

    def execute(self, plan):
        metric = plan.spec.metric_names[0]
        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence=self.evidence,
            plan=plan,
            rows=[{metric: str(self.values.pop(0))}],
            columns=[metric],
            validation="FAKE_ANOMALY_RUNTIME",
        )


class LensExecutor:
    def __init__(self, payloads=None, fail_dimensions=()):
        self.payloads = payloads or {}
        self.fail_dimensions = set(fail_dimensions)
        self.calls = []

    def execute(self, plan: SemanticQueryPlan):
        self.calls.append(plan)
        dimension = plan.spec.group_by[0]
        if dimension in self.fail_dimensions:
            return SemanticQueryResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                warnings=["MetricFlow Explain rejected this driver lens."],
                validation="FAKE_EXPLAIN_REJECTED",
            )
        current = plan.spec.start_time.startswith("2026-08-05")
        rows = self.payloads[dimension]["current" if current else "reference"]
        columns = [dimension, plan.spec.metric_names[0]]
        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            rows=rows,
            columns=columns,
            validation="FAKE_DRIVER_RUNTIME",
        )


def health(state=OperationalHealthState.HEALTHY, evidence="RUNTIME_VERIFIED"):
    return OperationalHealthSnapshot(state=state, evidence=evidence, details="phase6b-test")


def anomaly(monkeypatch: pytest.MonkeyPatch, *, metric="gross_sales", current=50, baseline_values=None, health_snapshot=None):
    baseline_values = baseline_values or [90, 100, 110, 95, 105, 100, 102]
    detector = GovernedAnomalyDetector(ROOT, executor=SequenceExecutor([current, *baseline_values]))
    spec = SemanticQuerySpec(
        metric=metric,
        metrics=(metric,),
        start_time="2026-08-05T00:00:00Z",
        end_time="2026-08-05T23:59:59Z",
        limit=20,
    )
    plan = detector.plan(spec, question=f"{metric} 异常吗？")
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    return detector.detect(plan, operational_health=health_snapshot or health())


def down_payloads(metric="gross_sales"):
    return {
        "store__region": {
            "current": [
                {"store__region": "West", metric: "20"},
                {"store__region": "South", metric: "30"},
            ],
            "reference": [
                {"store__region": "West", metric: "60"},
                {"store__region": "South", metric: "40"},
            ],
        },
        "item__brand": {
            "current": [
                {"item__brand": "Coca-Cola", metric: "20"},
                {"item__brand": "Generic", metric: "30"},
            ],
            "reference": [
                {"item__brand": "Coca-Cola", metric: "50"},
                {"item__brand": "Generic", metric: "50"},
            ],
        },
        "item__category": {
            "current": [
                {"item__category": "Beverage", metric: "10"},
                {"item__category": "Snack", metric: "40"},
            ],
            "reference": [
                {"item__category": "Beverage", metric: "60"},
                {"item__category": "Snack", metric: "40"},
            ],
        },
    }


def test_phase6b_policy_is_fail_closed_and_lenses_are_independent():
    policy = yaml.safe_load((ROOT / "agent/contracts/driver_attribution_policy.yml").read_text())
    assert policy["principles"]["phase6a_runtime_verified_anomaly_required"] is True
    assert policy["principles"]["verified_healthy_operational_state_required"] is True
    assert policy["principles"]["contributions_must_not_be_summed_across_lenses"] is True
    assert policy["principles"]["arbitrary_sql"] is False
    assert policy["limits"]["max_driver_dimensions"] == 3
    assert policy["runtime"]["allow_env"] == "PHASE6B_ALLOW_DRIVER_ATTRIBUTION"


def test_plan_uses_exact_phase6a_median_reference_window(monkeypatch):
    detected = anomaly(monkeypatch)
    assert detected.reference_window_index == 2
    plan = GovernedDriverAttribution(ROOT).plan(detected)
    assert plan.status is DriverAttributionStatus.READY
    assert [lens.dimension for lens in plan.lenses] == ["store__region", "item__brand", "item__category"]
    assert all(lens.current_spec.start_time == "2026-08-05T00:00:00Z" for lens in plan.lenses)
    assert all(lens.reference_spec.start_time == "2026-08-03T00:00:00Z" for lens in plan.lenses)
    assert all(lens.current_spec.group_by == (lens.dimension,) for lens in plan.lenses)


def test_plan_blocks_when_phase6a_does_not_prove_healthy_business_signal(monkeypatch):
    detected = anomaly(monkeypatch, health_snapshot=health(OperationalHealthState.UNHEALTHY))
    plan = GovernedDriverAttribution(ROOT).plan(detected)
    assert plan.status is DriverAttributionStatus.BLOCKED
    assert "BUSINESS_SIGNAL_SUSPECTED" in plan.warnings[0]


def test_runtime_gate_defers_before_any_driver_query(monkeypatch):
    detected = anomaly(monkeypatch)
    executor = LensExecutor(down_payloads())
    engine = GovernedDriverAttribution(ROOT, executor=executor)
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "false")
    result = engine.execute(detected)
    assert result.status is DriverAttributionStatus.DEFERRED
    assert result.validation == "NOT_EXECUTED"
    assert executor.calls == []


def test_down_anomaly_ranks_most_negative_driver_and_reconciles_contribution(monkeypatch):
    detected = anomaly(monkeypatch)
    engine = GovernedDriverAttribution(ROOT, executor=LensExecutor(down_payloads()))
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    assert result.status is DriverAttributionStatus.COMPLETE
    region = next(lens for lens in result.lenses if lens.dimension == "store__region")
    assert region.rows[0].dimension_value == "West"
    assert region.rows[0].absolute_change == "-40"
    assert region.rows[0].contribution_percent == "80"
    assert region.rows[0].rank == 1
    assert region.validation.endswith("RECONCILED")


def test_up_anomaly_ranks_most_positive_driver(monkeypatch):
    detected = anomaly(monkeypatch, current=150, baseline_values=[90, 100, 110, 95, 105, 100, 102])
    payloads = down_payloads()
    payloads["store__region"] = {
        "current": [
            {"store__region": "West", "gross_sales": "100"},
            {"store__region": "South", "gross_sales": "50"},
        ],
        "reference": [
            {"store__region": "West", "gross_sales": "60"},
            {"store__region": "South", "gross_sales": "40"},
        ],
    }
    payloads["item__brand"] = {
        "current": [{"item__brand": "Coca-Cola", "gross_sales": "90"}, {"item__brand": "Generic", "gross_sales": "60"}],
        "reference": [{"item__brand": "Coca-Cola", "gross_sales": "50"}, {"item__brand": "Generic", "gross_sales": "50"}],
    }
    payloads["item__category"] = {
        "current": [{"item__category": "Beverage", "gross_sales": "100"}, {"item__category": "Snack", "gross_sales": "50"}],
        "reference": [{"item__category": "Beverage", "gross_sales": "60"}, {"item__category": "Snack", "gross_sales": "40"}],
    }
    engine = GovernedDriverAttribution(ROOT, executor=LensExecutor(payloads))
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    region = next(lens for lens in result.lenses if lens.dimension == "store__region")
    assert region.rows[0].dimension_value == "West"
    assert region.rows[0].absolute_change == "40"
    assert region.rows[0].rank == 1


def test_cross_dimension_summary_keeps_lenses_separate_and_never_cross_sums(monkeypatch):
    detected = anomaly(monkeypatch)
    engine = GovernedDriverAttribution(ROOT, executor=LensExecutor(down_payloads()))
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    payload = result.to_dict()
    strongest = payload["strongest_driver_by_dimension"]
    assert strongest["store__region"]["dimension_value"] == "West"
    assert strongest["item__brand"]["dimension_value"] == "Coca-Cola"
    assert strongest["item__category"]["dimension_value"] == "Beverage"
    assert "cross-summed" in result.warnings[-1]
    assert "combined_contribution_percent" not in payload


def test_non_additive_metric_has_no_contribution_and_missing_side_is_not_zero(monkeypatch):
    detected = anomaly(monkeypatch, metric="average_order_value", current=50)
    payloads = {
        dimension: {
            "current": [
                {dimension: "West", "average_order_value": "40"},
                {dimension: "South", "average_order_value": "60"},
            ],
            "reference": [{dimension: "West", "average_order_value": "80"}],
        }
        for dimension in detected.driver_plan.dimensions
    }
    engine = GovernedDriverAttribution(ROOT, executor=LensExecutor(payloads))
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    assert result.status is DriverAttributionStatus.COMPLETE
    lens = result.lenses[0]
    assert lens.additive is False
    west = next(row for row in lens.rows if row.dimension_value == "West")
    assert west.contribution_percent is None
    # South is absent from the reference window; it is not coerced to reference=0 for AOV.
    assert all(row.dimension_value != "South" for row in lens.rows)
    assert any("not coerced to zero" in warning for warning in lens.warnings)


def test_one_failed_lens_returns_partial_without_erasing_verified_lenses(monkeypatch):
    detected = anomaly(monkeypatch)
    engine = GovernedDriverAttribution(
        ROOT,
        executor=LensExecutor(down_payloads(), fail_dimensions={"item__brand"}),
    )
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    assert result.status is DriverAttributionStatus.PARTIAL
    by_dim = {lens.dimension: lens for lens in result.lenses}
    assert by_dim["store__region"].status is DriverAttributionStatus.COMPLETE
    assert by_dim["item__brand"].status is DriverAttributionStatus.BLOCKED
    assert by_dim["item__category"].status is DriverAttributionStatus.COMPLETE


def test_all_failed_lenses_return_error(monkeypatch):
    detected = anomaly(monkeypatch)
    engine = GovernedDriverAttribution(
        ROOT,
        executor=LensExecutor(down_payloads(), fail_dimensions=set(detected.driver_plan.dimensions)),
    )
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    assert result.status is DriverAttributionStatus.ERROR
    assert result.validation == "NO_DRIVER_LENS_COMPLETED"


def test_additive_lens_fails_closed_when_grouped_values_do_not_reconcile(monkeypatch):
    detected = anomaly(monkeypatch)
    detected.driver_plan = replace(detected.driver_plan, dimensions=("store__region",))
    payloads = down_payloads()
    payloads["store__region"]["current"] = [{"store__region": "West", "gross_sales": "60"}]
    payloads["store__region"]["reference"] = [{"store__region": "West", "gross_sales": "100"}]
    engine = GovernedDriverAttribution(ROOT, executor=LensExecutor(payloads))
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    assert result.status is DriverAttributionStatus.ERROR
    assert result.lenses[0].validation == "DRIVER_RECONCILIATION_FAILED"


def test_member_limit_hit_fails_closed(monkeypatch):
    detected = anomaly(monkeypatch)
    detected.driver_plan = replace(detected.driver_plan, dimensions=("store__region",))
    rows_current = [{"store__region": f"R{i:02d}", "gross_sales": "1"} for i in range(50)]
    rows_reference = [{"store__region": f"R{i:02d}", "gross_sales": "2"} for i in range(50)]
    engine = GovernedDriverAttribution(
        ROOT,
        executor=LensExecutor({"store__region": {"current": rows_current, "reference": rows_reference}}),
    )
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    result = engine.execute(detected)
    assert result.status is DriverAttributionStatus.ERROR
    assert result.lenses[0].validation == "DRIVER_MEMBER_LIMIT_REACHED"
