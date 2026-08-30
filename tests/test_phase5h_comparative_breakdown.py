from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.analysis_session import AnalysisSessionStatus, GovernedAnalysisSession, SessionDeltaKind
from agent.breakdown_analysis import (
    BreakdownAnalysisMode,
    GovernedComparativeBreakdown,
    MetricContributionSemantics,
)
from agent.semantic_query import (
    GovernedSemanticQueryPlanner,
    SemanticQueryPlan,
    SemanticQueryResult,
    SemanticQueryStatus,
)
from agent.time_context import ComparisonMode, TimeComparisonContext

ROOT = Path(__file__).resolve().parents[1]


def grouped_plan(metric: str = "gross_sales"):
    return GovernedSemanticQueryPlanner(ROOT).plan(
        metric=metric,
        question=f"2026-08-01 到 2026-08-05 按地区看 {metric}",
    )


def test_phase5h_policy_is_bounded_runtime_verified_and_no_sql():
    policy = yaml.safe_load((ROOT / "agent/contracts/comparative_breakdown_policy.yml").read_text())
    assert policy["version"] == 1
    assert policy["principles"]["exactly_one_non_time_breakdown_dimension"] is True
    assert policy["principles"]["contribution_requires_additive_metric"] is True
    assert policy["principles"]["contribution_requires_aggregate_reconciliation"] is True
    assert policy["principles"]["arbitrary_sql"] is False
    assert policy["limits"]["max_breakdown_members"] == 50
    assert policy["runtime"]["allow_env"] == "PHASE5H_ALLOW_BREAKDOWN_QUERY"


def test_metric_additivity_is_derived_from_metricflow_definitions_not_duplicated_formula():
    semantics = MetricContributionSemantics(ROOT)
    assert semantics.is_additive("gross_sales") is True
    assert semantics.is_additive("activity_net_sales") is True
    assert semantics.is_additive("average_order_value") is False
    assert semantics.is_additive("order_count") is False
    assert semantics.is_additive("average_time_to_ship_minutes") is False


def test_breakdown_plan_keeps_region_and_derives_previous_equal_window():
    primary = grouped_plan().spec
    assert primary is not None
    plan = GovernedComparativeBreakdown(ROOT).plan(
        primary,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD, requested_days=5),
        question="各地区和前5天比呢？",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.dimension == "store__region"
    assert plan.current_spec.group_by == ("store__region",)
    assert plan.current_spec.start_time == "2026-08-01T00:00:00Z"
    assert plan.comparison_spec.start_time == "2026-07-27T00:00:00Z"
    assert plan.comparison_spec.end_time == "2026-07-31T23:59:59Z"


def test_time_grain_is_removed_from_period_breakdown_math_but_dimension_is_kept():
    primary = GovernedSemanticQueryPlanner(ROOT).plan(
        metric="gross_sales",
        question="2026-08-01 到 2026-08-05 按天按地区看 gross_sales",
    ).spec
    plan = GovernedComparativeBreakdown(ROOT).plan(
        primary,
        context=TimeComparisonContext(ComparisonMode.YEAR_OVER_YEAR),
        question="各地区同比呢？",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.current_spec.group_by == ("store__region",)
    assert any("temporal display grain" in warning for warning in plan.warnings)


def test_two_business_breakdown_dimensions_require_clarification():
    primary = GovernedSemanticQueryPlanner(ROOT).plan(
        metric="gross_sales",
        question="2026-08-01 到 2026-08-05 按地区按品牌看 gross_sales",
    ).spec
    plan = GovernedComparativeBreakdown(ROOT).plan(
        primary,
        context=TimeComparisonContext(ComparisonMode.YEAR_OVER_YEAR),
        question="同比呢？",
    )
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert "exactly one" in plan.warnings[0]


def test_contribution_blocks_non_additive_average_order_value():
    primary = grouped_plan("average_order_value").spec
    plan = GovernedComparativeBreakdown(ROOT).plan(
        primary,
        context=TimeComparisonContext(ComparisonMode.YEAR_OVER_YEAR),
        question="各地区谁贡献最大？",
        mode=BreakdownAnalysisMode.CONTRIBUTION,
    )
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert "not contribution-additive" in plan.warnings[0]


def test_breakdown_runtime_gate_defers_before_any_metricflow_query(monkeypatch: pytest.MonkeyPatch):
    engine = GovernedComparativeBreakdown(ROOT)
    plan = engine.plan(
        grouped_plan().spec,
        context=TimeComparisonContext(ComparisonMode.YEAR_OVER_YEAR),
        question="各地区同比呢？",
    )
    monkeypatch.delenv("PHASE5H_ALLOW_BREAKDOWN_QUERY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("MetricFlow must not run before Phase 5H gate")

    monkeypatch.setattr(engine.executor, "execute", forbidden)
    result = engine.execute(plan)
    assert result.status is SemanticQueryStatus.DEFERRED
    assert result.validation == "NOT_EXECUTED"


def _runtime_result(query_plan: SemanticQueryPlan, rows: list[dict[str, str]], columns: list[str]):
    return SemanticQueryResult(
        status=SemanticQueryStatus.COMPLETE,
        evidence="RUNTIME_VERIFIED",
        plan=query_plan,
        rows=rows,
        columns=columns,
        validation="FAKE_RUNTIME_VERIFIED",
    )


def test_runtime_grouped_comparison_outer_joins_new_and_lost_members(monkeypatch: pytest.MonkeyPatch):
    engine = GovernedComparativeBreakdown(ROOT)
    plan = engine.plan(
        grouped_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="各地区环比怎么样？",
    )
    monkeypatch.setenv("PHASE5H_ALLOW_BREAKDOWN_QUERY", "true")

    def fake_execute(query_plan: SemanticQueryPlan):
        if query_plan.spec.start_time.startswith("2026-08-01"):
            rows = [
                {"store__region": "West", "gross_sales": "150"},
                {"store__region": "South", "gross_sales": "80"},
                {"store__region": "New", "gross_sales": "20"},
            ]
        else:
            rows = [
                {"store__region": "West", "gross_sales": "100"},
                {"store__region": "South", "gross_sales": "100"},
                {"store__region": "Old", "gross_sales": "10"},
            ]
        return _runtime_result(query_plan, rows, ["store__region", "gross_sales"])

    monkeypatch.setattr(engine.executor, "execute", fake_execute)
    result = engine.execute(plan)
    assert result.status is SemanticQueryStatus.COMPLETE
    by_member = {row.dimension_value: row for row in result.rows}
    assert by_member["West"].absolute_change == "50"
    assert by_member["South"].absolute_change == "-20"
    assert by_member["New"].comparison_value == "0"
    assert by_member["New"].absolute_change == "20"
    assert by_member["Old"].current_value == "0"
    assert by_member["Old"].absolute_change == "-10"


def test_top_growth_amount_is_ranked_by_absolute_change(monkeypatch: pytest.MonkeyPatch):
    engine = GovernedComparativeBreakdown(ROOT)
    plan = engine.plan(
        grouped_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="哪个地区增长最多？",
        mode=BreakdownAnalysisMode.TOP_ABSOLUTE_CHANGE,
    )
    monkeypatch.setenv("PHASE5H_ALLOW_BREAKDOWN_QUERY", "true")

    def fake_execute(query_plan):
        rows = (
            [{"store__region": "West", "gross_sales": "150"}, {"store__region": "South", "gross_sales": "80"}]
            if query_plan.spec.start_time.startswith("2026-08-01")
            else [{"store__region": "West", "gross_sales": "100"}, {"store__region": "South", "gross_sales": "100"}]
        )
        return _runtime_result(query_plan, rows, ["store__region", "gross_sales"])

    monkeypatch.setattr(engine.executor, "execute", fake_execute)
    result = engine.execute(plan)
    assert [(row.rank, row.dimension_value, row.absolute_change) for row in result.rows] == [
        (1, "West", "50"),
        (2, "South", "-20"),
    ]


def test_contribution_reconciles_grouped_change_to_aggregate_before_percentages(monkeypatch: pytest.MonkeyPatch):
    engine = GovernedComparativeBreakdown(ROOT)
    plan = engine.plan(
        grouped_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="总增长主要是谁贡献的？",
        mode=BreakdownAnalysisMode.CONTRIBUTION,
    )
    monkeypatch.setenv("PHASE5H_ALLOW_BREAKDOWN_QUERY", "true")

    def fake_execute(query_plan):
        current = query_plan.spec.start_time.startswith("2026-08-01")
        if query_plan.spec.group_by:
            rows = (
                [
                    {"store__region": "West", "gross_sales": "150"},
                    {"store__region": "South", "gross_sales": "80"},
                    {"store__region": "New", "gross_sales": "20"},
                ]
                if current
                else [
                    {"store__region": "West", "gross_sales": "100"},
                    {"store__region": "South", "gross_sales": "100"},
                    {"store__region": "Old", "gross_sales": "10"},
                ]
            )
            return _runtime_result(query_plan, rows, ["store__region", "gross_sales"])
        value = "250" if current else "210"
        return _runtime_result(query_plan, [{"gross_sales": value}], ["gross_sales"])

    monkeypatch.setattr(engine.executor, "execute", fake_execute)
    result = engine.execute(plan)
    assert result.status is SemanticQueryStatus.COMPLETE
    assert result.validation == "GROUPED_WINDOWS_RUNTIME_VERIFIED_AND_CONTRIBUTION_RECONCILED"
    by_member = {row.dimension_value: row for row in result.rows}
    assert by_member["West"].contribution_percent == "125"
    assert by_member["New"].contribution_percent == "50"
    assert by_member["South"].contribution_percent == "-50"
    assert by_member["Old"].contribution_percent == "-25"
    assert result.rows[0].dimension_value == "West"
    assert result.rows[0].rank == 1


def test_contribution_fails_closed_when_grouped_rows_do_not_reconcile_to_total(monkeypatch: pytest.MonkeyPatch):
    engine = GovernedComparativeBreakdown(ROOT)
    plan = engine.plan(
        grouped_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="总增长是谁贡献的？",
        mode=BreakdownAnalysisMode.CONTRIBUTION,
    )
    monkeypatch.setenv("PHASE5H_ALLOW_BREAKDOWN_QUERY", "true")

    def fake_execute(query_plan):
        current = query_plan.spec.start_time.startswith("2026-08-01")
        if query_plan.spec.group_by:
            rows = [{"store__region": "West", "gross_sales": "150" if current else "100"}]
            return _runtime_result(query_plan, rows, ["store__region", "gross_sales"])
        # aggregate change = 80, grouped change = 50
        value = "180" if current else "100"
        return _runtime_result(query_plan, [{"gross_sales": value}], ["gross_sales"])

    monkeypatch.setattr(engine.executor, "execute", fake_execute)
    result = engine.execute(plan)
    assert result.status is SemanticQueryStatus.ERROR
    assert result.validation == "CONTRIBUTION_RECONCILIATION_FAILED"


def test_grouped_session_yoy_uses_breakdown_instead_of_phase5g_aggregate_rejection():
    manager = GovernedAnalysisSession(ROOT)
    state = manager.start(grouped_plan())
    result = manager.apply_follow_up(state, question="同比呢？")
    assert result.status is AnalysisSessionStatus.READY
    assert result.delta_kind is SessionDeltaKind.SET_COMPARISON
    assert result.state.comparison.mode is ComparisonMode.YEAR_OVER_YEAR
    assert result.breakdown_plan is not None
    assert result.breakdown_plan.dimension == "store__region"
    assert result.comparison_plan is None


def test_grouped_session_can_rank_then_request_contribution_without_reparsing_original(monkeypatch: pytest.MonkeyPatch):
    manager = GovernedAnalysisSession(ROOT)
    compared = manager.apply_follow_up(manager.start(grouped_plan()), question="同比呢？")

    def forbidden(*args, **kwargs):
        raise AssertionError("breakdown follow-up must not reparse original query")

    monkeypatch.setattr(manager.planner, "plan", forbidden)
    monkeypatch.setattr(manager.planner, "plan_metrics", forbidden)
    ranked = manager.apply_follow_up(compared.state, question="哪个地区增长最多？")
    assert ranked.status is AnalysisSessionStatus.READY
    assert ranked.delta_kind is SessionDeltaKind.RANK_BREAKDOWN
    assert ranked.breakdown_plan.mode is BreakdownAnalysisMode.TOP_ABSOLUTE_CHANGE
    contribution = manager.apply_follow_up(ranked.state, question="总增长主要是谁贡献的？")
    assert contribution.status is AnalysisSessionStatus.READY
    assert contribution.delta_kind is SessionDeltaKind.CONTRIBUTION_ANALYSIS
    assert contribution.breakdown_plan.mode is BreakdownAnalysisMode.CONTRIBUTION
    assert contribution.state.current_spec == compared.state.current_spec
    assert contribution.state.comparison == compared.state.comparison


def test_contribution_with_multi_metric_session_requires_metric_clarification():
    manager = GovernedAnalysisSession(ROOT)
    state = manager.start(grouped_plan())
    added = manager.apply_follow_up(state, question="再加上 AOV")
    compared = manager.apply_follow_up(added.state, question="同比呢？")
    # Setting the comparison itself is allowed as a multi-metric breakdown comparison.
    assert compared.status is AnalysisSessionStatus.READY
    result = manager.apply_follow_up(compared.state, question="谁贡献最大？")
    assert result.status is AnalysisSessionStatus.CLARIFICATION_REQUIRED
    assert result.state == compared.state
    assert "exactly one governed metric" in result.warnings[0]


def test_breakdown_session_mutation_is_not_exposed_as_public_llm_tool():
    import json

    schemas = json.loads((ROOT / "agent/contracts/tool_schemas.json").read_text())
    names = {item["name"] for item in schemas["tools"]}
    assert "compare_breakdown" not in names
    assert "rank_breakdown" not in names
    assert "compute_contribution" not in names
