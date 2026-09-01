"""Context Loader + Progressive Context Expansion 集成契约测试。"""

from pathlib import Path

from agent.context import (
    ContextBundleStatus,
    ContextExpansionReason,
    ContextItemStatus,
    ContextSource,
    GovernedContextLoader,
    GovernedContextPlanner,
    GovernedProgressiveContextExpander,
)
from agent.router import DeterministicToolRouter


ROOT = Path(__file__).resolve().parents[1]


def _prepare(question: str):
    route = DeterministicToolRouter(ROOT).plan(question)
    plan = GovernedContextPlanner(ROOT).plan(route)
    bundle = GovernedContextLoader(ROOT).load(route, plan)
    return route, plan, bundle


def test_metric_query_materializes_semantic_only():
    _, plan, bundle = _prepare("2026-08-05 gross_sales 是多少？")

    assert bundle.status is ContextBundleStatus.READY
    assert bundle.has_loaded(ContextSource.SEMANTIC)
    assert not bundle.has_loaded(ContextSource.CODE)
    assert not plan.requires(ContextSource.CODE)
    assert bundle.estimated_tokens > 0


def test_analysis_loads_semantic_and_skill_but_not_optional_code():
    _, plan, bundle = _prepare(
        "为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？"
    )

    assert bundle.status is ContextBundleStatus.READY
    assert bundle.has_loaded(ContextSource.SEMANTIC)
    assert bundle.has_loaded(ContextSource.SKILL)
    assert ContextSource.CODE in plan.optional_sources()

    code_items = bundle.items_for(ContextSource.CODE)
    assert len(code_items) == 1
    assert code_items[0].status is ContextItemStatus.NOT_LOADED


def test_analysis_progressively_expands_to_model_context_card():
    route, _, bundle = _prepare(
        "为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？"
    )
    expander = GovernedProgressiveContextExpander(ROOT)

    expanded = expander.expand_code(
        bundle,
        route,
        reason=ContextExpansionReason.TRANSFORMATION_LOGIC_REQUIRED,
    )

    assert expanded.status is ContextBundleStatus.READY
    assert expanded.has_loaded(ContextSource.CODE)
    assert expanded.expansion_count == 1

    model_items = [
        item for item in expanded.loaded_items(ContextSource.CODE)
        if item.key.startswith("model_context:")
    ]
    assert len(model_items) == 1
    assert model_items[0].key == "model_context:order_items"
    assert model_items[0].estimated_tokens > 0


def test_raw_code_requires_model_context_first():
    route, _, bundle = _prepare(
        "为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？"
    )
    expander = GovernedProgressiveContextExpander(ROOT)

    blocked = expander.expand_raw_code(
        bundle,
        model="order_items",
        start_line=1,
        end_line=20,
        reason=ContextExpansionReason.MODEL_CONTEXT_CARD_INSUFFICIENT,
    )
    assert blocked.status is ContextBundleStatus.PARTIAL
    assert "before a fresh Model Context Card" in blocked.warnings[-1]

    with_card = expander.expand_code(
        bundle,
        route,
        model="order_items",
        reason=ContextExpansionReason.TRANSFORMATION_LOGIC_REQUIRED,
    )
    with_raw = expander.expand_raw_code(
        with_card,
        model="order_items",
        start_line=1,
        end_line=20,
        reason=ContextExpansionReason.MODEL_CONTEXT_CARD_INSUFFICIENT,
    )

    assert with_raw.expansion_count == 2
    raw = [
        item for item in with_raw.loaded_items(ContextSource.CODE)
        if item.key.startswith("raw_code:")
    ]
    assert len(raw) == 1
    assert raw[0].evidence_mode == "BOUNDED_RAW_CODE"


def test_lineage_context_loads_metadata_and_keeps_code_optional():
    _, plan, bundle = _prepare("orders 的上游血缘是什么？")

    assert bundle.status is ContextBundleStatus.READY
    assert bundle.has_loaded(ContextSource.METADATA)
    assert ContextSource.CODE in plan.optional_sources()
    assert not bundle.has_loaded(ContextSource.CODE)


def test_runtime_context_is_bound_to_executor_not_prequeried():
    _, _, bundle = _prepare("为什么 orders 昨天没更新？")

    assert bundle.has_loaded(ContextSource.METADATA)
    assert bundle.executor_owned(ContextSource.RUNTIME)

    runtime = bundle.items_for(ContextSource.RUNTIME)
    assert len(runtime) == 1
    assert runtime[0].status is ContextItemStatus.EXECUTOR_OWNED
    assert runtime[0].estimated_tokens == 0
    assert runtime[0].payload["tool"] == "get_runtime_context"


def test_knowledge_context_is_bound_to_existing_router_steps():
    _, _, bundle = _prepare("这个项目的设计取舍是什么？")

    assert bundle.status is ContextBundleStatus.READY
    assert bundle.executor_owned(ContextSource.KNOWLEDGE)
    item = bundle.items_for(ContextSource.KNOWLEDGE)[0]
    assert item.status is ContextItemStatus.EXECUTOR_OWNED
    assert item.payload["tool"] == "router_planned_knowledge_steps"


def test_code_cannot_expand_when_context_plan_did_not_authorize_it():
    route, _, bundle = _prepare("2026-08-05 gross_sales 是多少？")
    expander = GovernedProgressiveContextExpander(ROOT)

    expanded = expander.expand_code(
        bundle,
        route,
        model="order_items",
        reason=ContextExpansionReason.TRANSFORMATION_LOGIC_REQUIRED,
    )

    assert expanded.status is ContextBundleStatus.PARTIAL
    assert "not an optional source" in expanded.warnings[-1]
