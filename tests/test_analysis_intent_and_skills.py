"""ANALYSIS Intent + Analytics Skill 的增量契约测试。"""

from pathlib import Path

from agent.context import ContextSource, GovernedContextPlanner
from agent.router import DeterministicToolRouter, ExecutionStatus, GovernedPlanExecutor, Intent, PlanStatus
from agent.skills import GovernedSkillRegistry, SkillResolutionStatus


ROOT = Path(__file__).resolve().parents[1]


def test_sales_decline_routes_to_analysis_before_metric_query():
    route = DeterministicToolRouter(ROOT).plan("为什么本周 gross_sales 下降？")

    assert route.intent is Intent.ANALYSIS
    assert route.status is PlanStatus.PLANNING_REQUIRED
    assert route.target_kind == "metric"
    assert route.target_id == "gross_sales"
    assert route.steps == []


def test_analysis_context_requires_semantic_and_one_skill_only():
    route = DeterministicToolRouter(ROOT).plan("为什么本周 gross_sales 下降？")
    context_plan = GovernedContextPlanner(ROOT).plan(route)

    assert set(context_plan.required_sources()) == {
        ContextSource.SEMANTIC,
        ContextSource.SKILL,
    }
    skill_req = next(item for item in context_plan.requirements if item.source is ContextSource.SKILL)
    assert skill_req.max_items == 1
    assert not context_plan.requires(ContextSource.CODE)
    assert not context_plan.requires(ContextSource.KNOWLEDGE)


def test_sales_decline_skill_resolves_deterministically():
    route = DeterministicToolRouter(ROOT).plan("为什么本周 gross_sales 下降？")
    resolution = GovernedSkillRegistry(ROOT).resolve(route)

    assert resolution.status is SkillResolutionStatus.RESOLVED
    assert resolution.skill is not None
    assert resolution.skill.skill_id == "sales_decline_analysis"
    assert resolution.skill.authority["metric_definition"] == "MetricFlow"
    assert resolution.skill.guardrails["arbitrary_sql"] is False
    assert "order_count" in resolution.skill.required_metrics
    assert "store__region" in resolution.skill.dimensions


def test_analysis_without_matching_skill_fails_closed():
    route = DeterministicToolRouter(ROOT).plan("为什么 shipment_count 下降？")
    assert route.intent is Intent.ANALYSIS

    resolution = GovernedSkillRegistry(ROOT).resolve(route)
    assert resolution.status is SkillResolutionStatus.NOT_FOUND
    assert resolution.skill is None


def test_old_executor_defers_uncompiled_analysis_instead_of_false_complete():
    route = DeterministicToolRouter(ROOT).plan("为什么本周 gross_sales 下降？")
    execution = GovernedPlanExecutor(ROOT).execute(route)

    assert execution.status is ExecutionStatus.DEFERRED
    assert execution.results == []
    assert any("Analysis plan has not been compiled" in item for item in execution.warnings)


def test_existing_metric_query_behavior_is_preserved():
    route = DeterministicToolRouter(ROOT).plan("2026-08-05 gross_sales 是多少？")

    assert route.intent is Intent.METRIC_QUERY
    assert route.status is PlanStatus.PLANNED
    assert route.steps[0].tool == "query_semantic_metric"


def test_metric_definition_still_wins_over_analysis():
    route = DeterministicToolRouter(ROOT).plan("gross_sales 怎么算？")

    assert route.intent is Intent.METRIC_DEFINITION
    assert route.status is PlanStatus.PLANNED


def test_dataset_runtime_still_wins_over_analysis_words():
    route = DeterministicToolRouter(ROOT).plan("orders 昨天为什么没更新？")

    assert route.intent is Intent.RUNTIME_DIAGNOSIS
    assert route.status is PlanStatus.PLANNED


def test_current_knowledge_intent_has_context_policy():
    route = DeterministicToolRouter(ROOT).plan("这个项目的设计原因是什么？")
    assert route.intent is Intent.KNOWLEDGE_QUERY

    context_plan = GovernedContextPlanner(ROOT).plan(route)
    assert context_plan.required_sources() == (ContextSource.KNOWLEDGE,)
