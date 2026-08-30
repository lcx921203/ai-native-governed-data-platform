from pathlib import Path
import json

import pytest

from agent.router import DeterministicToolRouter, GovernedPlanExecutor, Intent
from agent.semantic_query import GovernedSemanticQueryPlanner, MetricFlowSemanticQueryExecutor, SemanticQueryStatus

ROOT = Path(__file__).resolve().parents[1]


def test_single_metric_query_requires_explicit_time():
    planner = GovernedSemanticQueryPlanner(ROOT)
    no_time = planner.plan(metric="gross_sales", question="gross_sales 是多少？")
    assert no_time.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    ready = planner.plan(metric="gross_sales", question="2026-08-05 gross_sales 是多少？")
    assert ready.status is SemanticQueryStatus.READY
    assert ready.spec.start_time == "2026-08-05T00:00:00Z"
    assert ready.spec.end_time == "2026-08-05T23:59:59Z"


def test_single_metric_query_is_governed_and_explain_first():
    planner = GovernedSemanticQueryPlanner(ROOT)
    plan = planner.plan(metric="activity_net_sales", question="2026-08-05 activity_net_sales 是多少？")
    assert plan.status is SemanticQueryStatus.READY
    assert planner.explain_args(plan.spec)[-2:] == ["--explain", "--show-dataflow-plan"]
    assert "--where" not in planner.command_args(plan.spec)


def test_ungoverned_metric_is_blocked():
    plan = GovernedSemanticQueryPlanner(ROOT).plan(metric="units_ordered", question="2026-08-05 units_ordered 是多少？")
    assert plan.status is SemanticQueryStatus.BLOCKED


def test_runtime_gate_returns_deferred_without_numeric_rows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PHASE5B_ALLOW_METRICFLOW_QUERY", raising=False)
    planner = GovernedSemanticQueryPlanner(ROOT)
    plan = planner.plan(metric="gross_sales", question="2026-08-05 gross_sales 是多少？")
    result = MetricFlowSemanticQueryExecutor(ROOT).execute(plan)
    assert result.status is SemanticQueryStatus.DEFERRED
    assert result.evidence == "STATIC_CONTRACT"
    assert result.rows == []


def test_router_and_tool_schema_expose_single_metric_query():
    plan = DeterministicToolRouter(ROOT).plan("2026-08-05 gross_sales 是多少？")
    assert plan.intent is Intent.METRIC_QUERY
    assert [step.tool for step in plan.steps] == ["query_semantic_metric"]
    execution = GovernedPlanExecutor(ROOT).execute(plan)
    assert execution.status.value == "DEFERRED"
    schemas = json.loads((ROOT / "agent/contracts/tool_schemas.json").read_text())
    assert "query_semantic_metric" in {item["name"] for item in schemas["tools"]}
