from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from agent.response import GovernedResponseComposer
from agent.response.contracts import AnswerStatus, ClaimKind
from agent.router import DeterministicToolRouter, GovernedPlanExecutor, Intent, PlanStatus
from agent.semantic_query.contracts import SemanticFilterOperator, SemanticQueryStatus
from agent.semantic_query.executor import MetricFlowSemanticQueryExecutor
from agent.semantic_query.planner import GovernedSemanticQueryPlanner


ROOT = Path(__file__).resolve().parents[1]


def planner() -> GovernedSemanticQueryPlanner:
    return GovernedSemanticQueryPlanner(ROOT)


def test_phase5b_policy_is_bounded_and_structured_only() -> None:
    policy = yaml.safe_load((ROOT / "agent/contracts/semantic_query_policy.yml").read_text())
    assert policy["version"] == 2
    assert policy["principles"]["structured_dimension_filters_only"] is True
    assert policy["principles"]["arbitrary_sql"] is False
    assert policy["principles"]["arbitrary_where_clause"] is False
    assert policy["limits"]["max_metrics"] == 3
    assert policy["limits"]["max_filters"] == 2
    assert policy["limits"]["max_group_by"] == 2


def test_filter_dimensions_are_real_semantic_dimensions() -> None:
    policy = yaml.safe_load((ROOT / "agent/contracts/semantic_query_policy.yml").read_text())
    semantic = yaml.safe_load(
        (ROOT / "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml").read_text()
    )
    actual: set[str] = set()
    for model in semantic["models"]:
        primary = None
        for column in model.get("columns", []):
            entity = column.get("entity") or {}
            if entity.get("type") == "primary":
                primary = entity.get("name")
                break
        if not primary:
            continue
        for column in model.get("columns", []):
            if column.get("dimension", {}).get("type") == "categorical":
                actual.add(f"{primary}__{column['name']}")
    assert set(policy["structured_filter_dimensions"]).issubset(actual)


def test_us_west_natural_language_becomes_two_governed_filters() -> None:
    plan = planner().plan(
        metric="gross_sales",
        question="2026-08-01 到 2026-08-05 美国西部地区 按天看 gross_sales",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.spec is not None
    assert [(f.dimension, f.operator, f.value) for f in plan.spec.filters] == [
        ("store__country", SemanticFilterOperator.EQ, "US"),
        ("store__region", SemanticFilterOperator.EQ, "West"),
    ]
    assert plan.spec.group_by == ("metric_time__day",)


def test_structured_filters_are_rendered_by_planner_not_user_where() -> None:
    plan = planner().plan(
        metric="gross_sales",
        question="2026-08-05 美国西部地区 gross_sales 是多少",
    )
    assert plan.spec is not None
    args = planner().command_args(plan.spec)
    assert args.count("--where") == 2
    assert "{{ Dimension('store__country') }} = 'US'" in args
    assert "{{ Dimension('store__region') }} = 'West'" in args
    assert not any("select " in token.lower() for token in args)


def test_raw_where_or_equals_predicate_remains_blocked() -> None:
    for question in [
        "2026-08-05 gross_sales where region='west'",
        "2026-08-05 gross_sales region=west",
    ]:
        plan = planner().plan(metric="gross_sales", question=question)
        assert plan.status is SemanticQueryStatus.BLOCKED
        assert plan.spec is None


def test_explicit_unknown_filter_value_requires_clarification_not_unfiltered_query() -> None:
    plan = planner().plan(
        metric="gross_sales",
        question="2026-08-05 只看地区为北部的 gross_sales",
    )
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert plan.spec is None
    assert "no governed canonical value" in plan.warnings[0]


def test_multi_metric_plan_is_one_shared_metricflow_query() -> None:
    plan = planner().plan_metrics(
        metrics=["gross_sales", "activity_net_sales", "average_order_value"],
        question=(
            "2026-08-01 到 2026-08-05 美国西部地区，按天看 "
            "gross_sales、activity_net_sales 和 average_order_value"
        ),
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.spec is not None
    assert plan.spec.metric_names == (
        "gross_sales",
        "activity_net_sales",
        "average_order_value",
    )
    args = planner().command_args(plan.spec)
    assert args[:4] == [
        "mf",
        "query",
        "--metrics",
        "gross_sales,activity_net_sales,average_order_value",
    ]
    assert args.count("--where") == 2
    assert "--explain" not in args
    assert planner().explain_args(plan.spec)[-2:] == ["--explain", "--show-dataflow-plan"]


def test_more_than_three_metrics_is_blocked() -> None:
    plan = planner().plan_metrics(
        metrics=["gross_sales", "activity_net_sales", "average_order_value", "order_count"],
        question="2026-08-05 看四个指标",
    )
    assert plan.status is SemanticQueryStatus.BLOCKED


def test_multi_metric_router_uses_new_bounded_tool() -> None:
    question = (
        "2026-08-01 到 2026-08-05 美国西部地区，按天看 "
        "毛销售额、活动净销售额和客单价"
    )
    plan = DeterministicToolRouter(ROOT).plan(question)
    assert plan.intent is Intent.METRIC_QUERY
    assert plan.status is PlanStatus.PLANNED
    assert plan.target_kind == "metric_set"
    assert plan.target_id == "gross_sales,activity_net_sales,average_order_value"
    assert [step.tool for step in plan.steps] == ["query_semantic_metrics"]
    assert plan.steps[0].arguments["metrics"] == [
        "gross_sales",
        "activity_net_sales",
        "average_order_value",
    ]


def test_static_multi_metric_execution_is_deferred_and_has_no_numeric_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHASE5A_ALLOW_METRICFLOW_QUERY", raising=False)
    monkeypatch.delenv("PHASE5B_ALLOW_METRICFLOW_QUERY", raising=False)
    question = "2026-08-01 到 2026-08-05 美国西部地区，按天看 毛销售额、活动净销售额和客单价"
    plan = DeterministicToolRouter(ROOT).plan(question)
    execution = GovernedPlanExecutor(ROOT).execute(plan)
    assert execution.status.value == "DEFERRED"
    result = execution.results[0]
    assert result["tool"] == "query_semantic_metrics"
    assert result["payload"]["rows"] == []
    envelope = GovernedResponseComposer(ROOT).compose(execution)
    assert envelope.status is AnswerStatus.DEFERRED
    query_plan = next(c for c in envelope.claims if c.kind is ClaimKind.SEMANTIC_QUERY_PLAN)
    assert "gross_sales,activity_net_sales,average_order_value" in query_plan.text
    assert "store__region EQ West" in query_plan.text
    assert not any(c.kind is ClaimKind.QUERY_RESULT for c in envelope.claims)


def test_multi_metric_tool_schema_is_bounded_and_contains_no_sql_or_where() -> None:
    schemas = json.loads((ROOT / "agent/contracts/tool_schemas.json").read_text())
    tool = next(item for item in schemas["tools"] if item["name"] == "query_semantic_metrics")
    props = tool["parameters"]["properties"]
    assert props["metrics"]["minItems"] == 2
    assert props["metrics"]["maxItems"] == 3
    assert props["limit"]["maximum"] == 50
    assert "sql" not in props
    assert "where" not in props


class MultiMetricFakeRunner:
    def __init__(self, *, explain_returncode: int = 0, omit_metric: str | None = None):
        self.explain_returncode = explain_returncode
        self.omit_metric = omit_metric
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if "--explain" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                self.explain_returncode,
                stdout="PLAN",
                stderr="semantic mismatch" if self.explain_returncode else "",
            )
        if "--csv" in cmd:
            metrics = ["gross_sales", "activity_net_sales", "average_order_value"]
            if self.omit_metric:
                metrics.remove(self.omit_metric)
            columns = ["metric_time__day", *metrics]
            values = ["2026-08-05", "100.00", "90.00", "45.00"][: len(columns)]
            path = Path(cmd[cmd.index("--csv") + 1])
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                writer.writerow(values)
            return subprocess.CompletedProcess(cmd, 0, stdout="OK", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")


def _runtime_executor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner) -> MetricFlowSemanticQueryExecutor:
    fake_mf = tmp_path / "mf"
    fake_mf.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_mf.chmod(0o755)
    monkeypatch.delenv("PHASE5A_ALLOW_METRICFLOW_QUERY", raising=False)
    monkeypatch.setenv("PHASE5B_ALLOW_METRICFLOW_QUERY", "true")
    monkeypatch.setenv("METRICFLOW_BIN", str(fake_mf))
    return MetricFlowSemanticQueryExecutor(ROOT, runner=runner)


def test_runtime_multi_metric_executor_explains_then_queries_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = MultiMetricFakeRunner()
    executor = _runtime_executor(monkeypatch, tmp_path, runner)
    plan = planner().plan_metrics(
        metrics=["gross_sales", "activity_net_sales", "average_order_value"],
        question="2026-08-05 美国西部地区 按天看 gross_sales activity_net_sales average_order_value",
    )
    result = executor.execute(plan)
    assert result.status is SemanticQueryStatus.COMPLETE
    assert result.evidence == "RUNTIME_VERIFIED"
    assert len(runner.calls) == 2
    assert "--explain" in runner.calls[0]
    assert "--csv" in runner.calls[1]
    assert runner.calls[0].count("--where") == 2
    assert "gross_sales,activity_net_sales,average_order_value" in runner.calls[0]


def test_metricflow_explain_rejection_blocks_multi_metric_data_query(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = MultiMetricFakeRunner(explain_returncode=1)
    executor = _runtime_executor(monkeypatch, tmp_path, runner)
    plan = planner().plan_metrics(
        metrics=["gross_sales", "activity_net_sales"],
        question="2026-08-05 按品牌看 gross_sales activity_net_sales",
    )
    result = executor.execute(plan)
    assert result.status is SemanticQueryStatus.BLOCKED
    assert result.validation == "METRICFLOW_EXPLAIN_REJECTED"
    assert len(runner.calls) == 1


def test_success_artifact_must_contain_every_requested_metric(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = MultiMetricFakeRunner(omit_metric="average_order_value")
    executor = _runtime_executor(monkeypatch, tmp_path, runner)
    plan = planner().plan_metrics(
        metrics=["gross_sales", "activity_net_sales", "average_order_value"],
        question="2026-08-05 按天看 gross_sales activity_net_sales average_order_value",
    )
    result = executor.execute(plan)
    assert result.status is SemanticQueryStatus.ERROR
    assert result.validation == "MISSING_METRIC_COLUMNS"


def test_generated_phase5b_sample_preserves_filters_and_deferred_evidence() -> None:
    payload = json.loads((ROOT / "agent/generated/semantic_query_samples.json").read_text())
    sample = payload["samples"]["filtered_multi_metric"]
    assert sample["route"]["steps"][0]["tool"] == "query_semantic_metrics"
    spec = sample["execution"]["results"][0]["payload"]["plan"]["spec"]
    assert spec["metrics"] == ["gross_sales", "activity_net_sales", "average_order_value"]
    assert [(f["dimension"], f["value"]) for f in spec["filters"]] == [
        ("store__country", "US"),
        ("store__region", "West"),
    ]
    assert sample["envelope"]["status"] == "DEFERRED"
