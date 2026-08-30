from __future__ import annotations

from pathlib import Path

import yaml

from agent.context.repository import GovernedContextRepository


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC = ROOT / "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml"
LIFECYCLE_METRICS = ROOT / "dbt/mercaso_dbt/models/metrics/lifecycle.yml"
REGISTRY = ROOT / "metadata/datahub/governance/metric_registry.yml"
ROUTING = ROOT / "agent/contracts/intent_routing.yml"
GENERATED_LEGACY = ROOT / "dbt/mercaso_metricflow_compat/models/_generated_semantic_legacy.yml"


def test_lifecycle_snapshot_is_semantic_model_at_order_grain():
    doc = yaml.safe_load(SEMANTIC.read_text(encoding="utf-8"))
    model = next(item for item in doc["models"] if item["name"] == "order_lifecycle_snapshot")
    assert model["semantic_model"]["enabled"] is True
    assert model["agg_time_dimension"] == "order_time"

    columns = {item["name"]: item for item in model["columns"]}
    assert columns["order_id"]["entity"] == {"name": "order", "type": "primary"}
    assert columns["store_id"]["entity"] == {"name": "store", "type": "foreign"}
    for name in (
        "order_time",
        "first_paid_at",
        "first_fulfillment_at",
        "first_in_transit_at",
        "first_delivered_at",
    ):
        assert columns[name]["dimension"]["type"] == "time"
        assert columns[name]["granularity"] == "hour"

    metrics = {item["name"]: item for item in model["metrics"]}
    assert metrics["lifecycle_order_count"]["agg"] == "count_distinct"
    assert metrics["paid_order_count"]["agg_time_dimension"] == "first_paid_at"
    assert metrics["fulfillment_started_order_count"]["agg_time_dimension"] == "first_fulfillment_at"
    assert metrics["delivered_order_count"]["agg_time_dimension"] == "first_delivered_at"


def test_lifecycle_conversion_metrics_use_same_order_entity_and_explicit_windows():
    doc = yaml.safe_load(LIFECYCLE_METRICS.read_text(encoding="utf-8"))
    metrics = {item["name"]: item for item in doc["metrics"]}

    expected = {
        "order_to_paid_24h_conversion_rate": ("paid_order_count", "24 hours"),
        "order_to_fulfillment_3d_conversion_rate": ("fulfillment_started_order_count", "3 days"),
        "order_to_delivered_7d_conversion_rate": ("delivered_order_count", "7 days"),
    }
    for name, (conversion_metric, window) in expected.items():
        metric = metrics[name]
        assert metric["type"] == "conversion"
        assert metric["entity"] == "order"
        assert metric["calculation"] == "conversion_rate"
        assert metric["base_metric"] == "lifecycle_order_count"
        assert metric["conversion_metric"] == conversion_metric
        assert metric["window"] == window


def test_lifecycle_conversion_metrics_are_governed_and_routable():
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    governed = {item["id"] for item in registry["metrics"]}
    routing = yaml.safe_load(ROUTING.read_text(encoding="utf-8"))["metric_aliases"]

    for metric in (
        "order_to_paid_24h_conversion_rate",
        "order_to_fulfillment_3d_conversion_rate",
        "order_to_delivered_7d_conversion_rate",
    ):
        assert metric in governed
        assert metric in routing


def test_agent_metric_context_resolves_conversion_dependencies_to_lifecycle_model():
    repository = GovernedContextRepository(ROOT)
    context = repository.metric_context("order_to_paid_24h_conversion_rate")
    assert context is not None
    assert context["definition"]["type"] == "conversion"
    assert context["definition"]["source_file"] == "dbt/mercaso_dbt/models/metrics/lifecycle.yml"
    assert context["related_models"] == ["order_lifecycle_snapshot"]


def test_generated_legacy_spec_contains_conversion_type_params():
    generated = yaml.safe_load(GENERATED_LEGACY.read_text(encoding="utf-8"))
    metrics = {item["name"]: item for item in generated["metrics"]}
    metric = metrics["order_to_paid_24h_conversion_rate"]
    params = metric["type_params"]["conversion_type_params"]
    assert params["entity"] == "order"
    assert params["base_measure"] == "lifecycle_order_count"
    assert params["conversion_measure"] == "paid_order_count"
    assert params["window"] == "24 hours"
    assert params["calculation"] == "conversion_rate"
