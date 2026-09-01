"""Context Planner 的静态契约测试。

测试刻意不依赖 Router 的构造函数：
- 只验证 Context Planner 对 Router 输出的消费行为；
- 避免 Context Planner 测试重复测试 Router 自己的 alias / marker 逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from agent.context.contracts import ContextSource
from agent.context.planner import GovernedContextPlanner


ROOT = Path(__file__).resolve().parents[1]


class FakeIntent(str, Enum):
    METRIC_QUERY = "METRIC_QUERY"
    RUNTIME_DIAGNOSIS = "RUNTIME_DIAGNOSIS"
    LINEAGE_QUERY = "LINEAGE_QUERY"
    UNKNOWN_NEW_INTENT = "UNKNOWN_NEW_INTENT"


@dataclass
class FakeRoute:
    intent: FakeIntent
    target_kind: str | None = None
    target_id: str | None = None


def test_metric_query_uses_minimal_semantic_context_only():
    planner = GovernedContextPlanner(ROOT)
    route = FakeRoute(FakeIntent.METRIC_QUERY, "metric", "gross_sales")

    context_plan = planner.plan(route)

    assert context_plan.route_intent == "METRIC_QUERY"
    assert context_plan.requires(ContextSource.SEMANTIC)
    assert not context_plan.requires(ContextSource.KNOWLEDGE)
    assert not context_plan.requires(ContextSource.CODE)
    assert not context_plan.requires(ContextSource.MEMORY)


def test_runtime_diagnosis_requires_metadata_and_runtime():
    planner = GovernedContextPlanner(ROOT)
    route = FakeRoute(FakeIntent.RUNTIME_DIAGNOSIS, "dataset", "orders")

    context_plan = planner.plan(route)

    assert context_plan.requires(ContextSource.METADATA)
    assert context_plan.requires(ContextSource.RUNTIME)
    assert set(context_plan.required_sources()) == {
        ContextSource.METADATA,
        ContextSource.RUNTIME,
    }


def test_lineage_keeps_code_optional():
    planner = GovernedContextPlanner(ROOT)
    route = FakeRoute(FakeIntent.LINEAGE_QUERY, "dataset", "orders")

    context_plan = planner.plan(route)

    assert ContextSource.METADATA in context_plan.required_sources()
    assert ContextSource.CODE in context_plan.optional_sources()


def test_unregistered_intent_fails_closed_without_loading_everything():
    planner = GovernedContextPlanner(ROOT)
    route = FakeRoute(FakeIntent.UNKNOWN_NEW_INTENT)

    context_plan = planner.plan(route)

    assert context_plan.requirements == ()
    assert context_plan.warnings
    assert "No governed context policy" in context_plan.warnings[0]
