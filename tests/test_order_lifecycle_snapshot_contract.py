from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "dbt/mercaso_dbt/models/marts/commerce/order_lifecycle_snapshot.sql"
SCHEMA = ROOT / "dbt/mercaso_dbt/models/marts/commerce/_commerce_models.yml"
SINGULAR_TEST = ROOT / "dbt/mercaso_dbt/tests/order_lifecycle_snapshot_milestone_order.sql"


def test_order_lifecycle_snapshot_is_one_row_per_order_incremental_merge():
    text = MODEL.read_text(encoding="utf-8")
    assert "materialized='incremental'" in text
    assert "incremental_strategy='merge'" in text
    assert "unique_key='order_id'" in text
    assert "partition_by='days(order_time)'" in text
    assert "'shopify_windowed'" in text


def test_order_lifecycle_snapshot_propagates_all_current_lifecycle_change_paths():
    text = MODEL.read_text(encoding="utf-8")
    for ref_name in (
        "int_shopify__orders_canonical",
        "int_shopify__transactions_canonical",
        "int_shopify__refunds_canonical",
        "int_shopify__fulfillments_canonical",
        "int_shopify__fulfillment_events_canonical",
    ):
        assert f"ref('{ref_name}')" in text

    for cte in (
        "changed_orders",
        "changed_transactions",
        "changed_refunds",
        "changed_fulfillments",
        "changed_fulfillment_events",
        "affected_order_ids",
    ):
        assert cte in text


def test_paid_milestone_requires_successful_collection_not_authorization():
    text = MODEL.read_text(encoding="utf-8")
    assert "transaction_status = 'SUCCESS'" in text
    assert "transaction_kind in ('CAPTURE', 'SALE')" in text
    assert "first_paid_at" in text
    assert "first_authorized_at" in text


def test_lifecycle_snapshot_does_not_invent_picked_at():
    text = MODEL.read_text(encoding="utf-8").lower()
    # The model may mention picked_at only in a comment explaining why it is absent.
    select_region = text.split("modeled as (", 1)[1]
    assert "picked_at" not in select_region


def test_fulfillment_milestones_use_explicit_source_times_and_events():
    text = MODEL.read_text(encoding="utf-8")
    assert "f.in_transit_at" in text
    assert "f.delivered_at" in text
    assert "e.event_status = 'IN_TRANSIT'" in text
    assert "e.event_status = 'DELIVERED'" in text
    assert "e.event_time" in text


def test_lifecycle_snapshot_schema_contract_exists():
    doc = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    models = {item["name"]: item for item in doc["models"]}
    snapshot = models["order_lifecycle_snapshot"]
    columns = {item["name"]: item for item in snapshot["columns"]}
    assert set(("order_id", "store_id", "order_time", "paid_flag", "shipped_flag", "delivered_flag")) <= set(columns)
    order_tests = columns["order_id"]["data_tests"]
    assert "not_null" in order_tests
    assert "unique" in order_tests


def test_lifecycle_snapshot_has_business_time_order_guard_for_future_runtime():
    text = SINGULAR_TEST.read_text(encoding="utf-8")
    assert "ref('order_lifecycle_snapshot')" in text
    for field in (
        "first_paid_at",
        "first_refund_at",
        "first_fulfillment_at",
        "first_in_transit_at",
        "first_delivered_at",
        "cancelled_at",
        "closed_at",
    ):
        assert field in text


def test_order_lifecycle_snapshot_is_in_governed_daily_freshness_recovery_sla():
    policy = (ROOT / "orchestration/dagster/commerce_dagster/consumer_sla.py").read_text(encoding="utf-8")
    freshness = (ROOT / "orchestration/dagster/commerce_dagster/freshness.py").read_text(encoding="utf-8")
    recovery_state = (ROOT / "orchestration/dagster/commerce_dagster/recovery_state_current.py").read_text(encoding="utf-8")

    assert '"order_lifecycle_snapshot"' in policy
    assert 'SHOPIFY_DAILY_MART_COUNT = len(SHOPIFY_DAILY_MART_ASSET_KEYS)' in policy
    assert '九张 Shopify Consumer Mart' in freshness
    assert 'SHOPIFY_DAILY_MART_ASSET_KEYS' in recovery_state


def test_order_lifecycle_snapshot_is_governed_and_semantic_ready_after_metric_approval():
    import json
    import yaml

    policy = yaml.safe_load((ROOT / "metadata/datahub/governance/asset_policy.yml").read_text(encoding="utf-8"))
    lifecycle = next(asset for asset in policy["assets"] if asset["model"] == "order_lifecycle_snapshot")
    assert "semantic-enabled" in lifecycle.get("tags", [])
    assert "commerce.governance.agentReadiness" not in lifecycle.get("structured_properties", {})
    for term in (
        "commerce-metric-order-to-paid-24h-conversion-rate",
        "commerce-metric-order-to-fulfillment-3d-conversion-rate",
        "commerce-metric-order-to-delivered-7d-conversion-rate",
    ):
        assert term in lifecycle.get("glossary_terms", [])

    identities = json.loads((ROOT / "metadata/datahub/generated/dataset_identity_resolution.json").read_text(encoding="utf-8"))
    identity = next(item for item in identities["identities"] if item["model"] == "order_lifecycle_snapshot")
    assert identity["status"] == "UNVERIFIED_EXPECTED"
    assert identity["resolved_urn"] is None

    projection = json.loads((ROOT / "metadata/datahub/generated/governance_projection.json").read_text(encoding="utf-8"))
    item = next(item for item in projection["items"] if item["model"] == "order_lifecycle_snapshot")
    assert item["status"] == "BLOCKED_IDENTITY_UNRESOLVED"
    assert item["structured_properties"]["commerce.governance.agentReadiness"] == "SEMANTIC_READY"

    runtime_verification = yaml.safe_load((ROOT / "infra/contracts/phase7/datahub_runtime_verification.yml").read_text(encoding="utf-8"))
    assert "order_lifecycle_snapshot" in runtime_verification["lineage"]["required_models"]

def test_current_r01_and_agent_runtime_contract_use_nine_mart_registry():
    r01 = (ROOT / "acceptance/phase3c/r01_normal_schedule.py").read_text(encoding="utf-8")
    repository = (ROOT / "agent/context/repository.py").read_text(encoding="utf-8")
    governed = (ROOT / "agent/tools/governed_metadata.py").read_text(encoding="utf-8")

    assert "from consumer_sla import SHOPIFY_DAILY_MART_ASSET_KEYS" in r01
    assert 'consumer_sla.py' in repository
    assert 'sla_module["SHOPIFY_DAILY_MART_ASSET_KEYS"]' in repository
    assert 'consumer_sla_contract' in governed

