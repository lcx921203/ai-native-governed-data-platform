"""Governed Analysis Planner 集成契约测试。

验证链路：
Router -> Context Planner -> Skill Registry -> Analysis Planner。
测试只检查“计划是否正确编译”，不会执行真实 MetricFlow 查询。
"""

from pathlib import Path

from agent.analysis_planner import AnalysisPlanStatus, AnalysisUnitKind, GovernedAnalysisPlanner
from agent.context import ContextPlan, ContextRequirement, ContextSource, GovernedContextPlanner
from agent.router import DeterministicToolRouter, Intent, PlanStatus


ROOT = Path(__file__).resolve().parents[1]


def _route_and_context(question: str):
    route = DeterministicToolRouter(ROOT).plan(question)
    context = GovernedContextPlanner(ROOT).plan(route)
    return route, context


def test_sales_decline_skill_compiles_to_bounded_analysis_units():
    question = "为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？"
    route, context = _route_and_context(question)

    assert route.intent is Intent.ANALYSIS
    assert route.status is PlanStatus.PLANNING_REQUIRED

    plan = GovernedAnalysisPlanner(ROOT).plan(route, context)

    assert plan.status is AnalysisPlanStatus.READY
    assert plan.skill_id == "sales_decline_analysis"
    assert plan.target_metric == "gross_sales"
    assert plan.executable is True

    kinds = [unit.kind for unit in plan.units]
    assert kinds.count(AnalysisUnitKind.TIME_COMPARISON) == 3
    assert kinds.count(AnalysisUnitKind.BREAKDOWN) == 3
    assert kinds.count(AnalysisUnitKind.EVIDENCE_SUMMARY) == 1

    # 最后一层只能汇总已有证据，不能创造新的 Metric Math。
    summary = plan.units[-1]
    assert summary.kind is AnalysisUnitKind.EVIDENCE_SUMMARY
    assert summary.compiled_plan["evidence_only"] is True
    assert summary.compiled_plan["no_new_metric_math"] is True
    assert len(summary.depends_on) == len(plan.units) - 1


def test_analysis_requires_explicit_comparison_baseline():
    question = "为什么 2026-08-01 到 2026-08-07 gross_sales 下降？"
    route, context = _route_and_context(question)

    plan = GovernedAnalysisPlanner(ROOT).plan(route, context)

    assert plan.status is AnalysisPlanStatus.CLARIFICATION_REQUIRED
    assert "环比" in plan.warnings[0]
    assert plan.units == ()


def test_analysis_requires_explicit_calendar_dates_before_semantic_planning():
    question = "为什么本周 gross_sales 环比下降？"
    route, context = _route_and_context(question)

    plan = GovernedAnalysisPlanner(ROOT).plan(route, context)

    assert plan.status is AnalysisPlanStatus.CLARIFICATION_REQUIRED
    assert any("explicit calendar date" in warning.lower() for warning in plan.warnings)


def test_analysis_planner_rejects_non_analysis_route():
    question = "2026-08-05 gross_sales 是多少？"
    route, context = _route_and_context(question)

    plan = GovernedAnalysisPlanner(ROOT).plan(route, context)

    assert route.intent is Intent.METRIC_QUERY
    assert plan.status is AnalysisPlanStatus.BLOCKED
    assert "Intent.ANALYSIS" in plan.warnings[0]


def test_analysis_planner_requires_semantic_and_skill_context():
    question = "为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？"
    route = DeterministicToolRouter(ROOT).plan(question)
    bad_context = ContextPlan(
        route_intent="ANALYSIS",
        target_kind="metric",
        target_id="gross_sales",
        requirements=(
            ContextRequirement(
                source=ContextSource.SEMANTIC,
                required=True,
                max_items=3,
                reason="test",
            ),
        ),
    )

    plan = GovernedAnalysisPlanner(ROOT).plan(route, bad_context)

    assert plan.status is AnalysisPlanStatus.BLOCKED
    assert "skill" in plan.warnings[0].lower()
