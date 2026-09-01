"""Model Context Card / Code-aware Context 契约测试。"""

from pathlib import Path

from agent.code_context import (
    GovernedModelContextRepository,
    ModelContextCardBuilder,
    ModelContextStatus,
)
from agent.context import ContextSource, GovernedContextPlanner
from agent.router import DeterministicToolRouter


ROOT = Path(__file__).resolve().parents[1]


def test_orders_card_is_compact_code_derived_context():
    builder = ModelContextCardBuilder(ROOT)
    card = builder.build("orders")

    assert card is not None
    assert card.config["materialized"] == "incremental"
    assert card.config["incremental_strategy"] == "merge"
    assert card.config["unique_key"] == "order_id"
    assert card.config["partition_by"] == "days(order_time)"

    assert "int_shopify__orders_canonical" in card.upstream_refs
    assert "source_updated_at" in card.execution_window_fields
    assert card.business_time == "order_time"

    entity_names = {(item["name"], item["type"]) for item in card.entities}
    assert ("order", "primary") in entity_names
    assert ("store", "foreign") in entity_names

    metric_names = {item["name"] for item in card.metrics}
    assert "order_count" in metric_names

    # Card 必须远小于“把完整工程上下文全部塞给 LLM”的做法。
    assert builder.estimate_tokens(card) > 0
    assert builder.estimate_tokens(card) < 1800


def test_lifecycle_card_finds_multi_source_code_dependencies():
    card = ModelContextCardBuilder(ROOT).build("order_lifecycle_snapshot")

    assert card is not None
    assert card.grain is not None
    assert "一个 Order 一行" in card.grain

    expected = {
        "int_shopify__orders_canonical",
        "int_shopify__transactions_canonical",
        "int_shopify__refunds_canonical",
        "int_shopify__fulfillments_canonical",
        "int_shopify__fulfillment_events_canonical",
    }
    assert expected.issubset(set(card.upstream_refs))
    assert card.config["unique_key"] == "order_id"
    assert card.business_time == "order_time"

    metric_names = {item["name"] for item in card.metrics}
    assert "lifecycle_order_count" in metric_names
    assert "paid_order_count" in metric_names
    assert "delivered_order_count" in metric_names


def test_repository_local_build_fallback_uses_no_prebuilt_card():
    repo = GovernedModelContextRepository(ROOT)
    result = repo.resolve("orders", allow_local_build_fallback=True)

    assert result.status is ModelContextStatus.RESOLVED
    assert result.card is not None
    assert result.evidence_mode in {"PREBUILT_CARD", "LOCAL_CODE_DERIVED"}
    assert result.estimated_tokens > 0


def test_raw_code_fallback_is_explicit_and_bounded():
    repo = GovernedModelContextRepository(ROOT)

    blocked = repo.raw_snippet(
        "orders",
        start_line=1,
        end_line=20,
        allow_raw_fallback=False,
    )
    assert blocked.status is ModelContextStatus.BLOCKED

    too_large = repo.raw_snippet(
        "orders",
        start_line=1,
        end_line=200,
        allow_raw_fallback=True,
    )
    assert too_large.status is ModelContextStatus.BLOCKED

    allowed = repo.raw_snippet(
        "orders",
        start_line=1,
        end_line=20,
        allow_raw_fallback=True,
    )
    assert allowed.status is ModelContextStatus.RESOLVED
    assert allowed.start_line == 1
    assert allowed.end_line <= 20
    assert "materialized='incremental'" in allowed.content


def test_analysis_context_keeps_code_optional_for_progressive_expansion():
    question = "为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？"
    route = DeterministicToolRouter(ROOT).plan(question)
    context_plan = GovernedContextPlanner(ROOT).plan(route)

    assert ContextSource.SEMANTIC in context_plan.required_sources()
    assert ContextSource.SKILL in context_plan.required_sources()
    assert ContextSource.CODE in context_plan.optional_sources()


def test_metric_query_does_not_load_code_context():
    question = "2026-08-05 gross_sales 是多少？"
    route = DeterministicToolRouter(ROOT).plan(question)
    context_plan = GovernedContextPlanner(ROOT).plan(route)

    assert ContextSource.SEMANTIC in context_plan.required_sources()
    assert not context_plan.requires(ContextSource.CODE)
