from pathlib import Path

import pytest

from agent.dimension_values import GovernedDimensionValuePlanner, MetricFlowDimensionValueExecutor, DimensionValueStatus
from agent.router import DeterministicToolRouter, GovernedPlanExecutor, Intent, PlanStatus
from agent.tools import GovernedMetadataTools

ROOT = Path(__file__).resolve().parents[1]


def test_dimension_value_discovery_requires_metric_context():
    plan = GovernedDimensionValuePlanner(ROOT).plan(metrics=[], dimension="store__region", question="有哪些地区可以筛？")
    assert plan.status is DimensionValueStatus.CLARIFICATION_REQUIRED


def test_static_seed_fallback_is_labeled_not_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PHASE5C_ALLOW_METRICFLOW_DISCOVERY", raising=False)
    planner = GovernedDimensionValuePlanner(ROOT)
    plan = planner.plan(metrics=["gross_sales"], dimension="store__region", question="gross_sales 有哪些地区可以筛？")
    result = MetricFlowDimensionValueExecutor(ROOT).execute(plan)
    assert result.status is DimensionValueStatus.DEFERRED
    assert result.evidence == "STATIC_CONTRACT"
    assert result.source_mode == "STATIC_SEED_FALLBACK"
    assert result.values == ["West", "South"]


def test_router_can_plan_dimension_value_discovery_with_metric_context():
    plan = DeterministicToolRouter(ROOT).plan("gross_sales 有哪些地区可以筛？")
    assert plan.intent is Intent.DIMENSION_VALUE_DISCOVERY
    assert plan.status is PlanStatus.PLANNED
    assert plan.steps[0].tool == "get_dimension_values"
    execution = GovernedPlanExecutor(ROOT).execute(plan)
    assert execution.status.value == "DEFERRED"


def test_router_does_not_invent_metric_context_for_dimension_values():
    plan = DeterministicToolRouter(ROOT).plan("有哪些地区可以筛？")
    assert plan.intent is Intent.DIMENSION_VALUE_DISCOVERY
    assert plan.status is PlanStatus.NEEDS_DISCOVERY
    assert plan.steps == []


def test_governed_tool_exposes_dimension_values_without_sql(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PHASE5C_ALLOW_METRICFLOW_DISCOVERY", raising=False)
    result = GovernedMetadataTools(ROOT).get_dimension_values(metrics=["gross_sales"], dimension="store__region")
    assert result["status"] == "DEFERRED"
    assert result["payload"]["values"] == ["West", "South"]
