from __future__ import annotations

import json
from pathlib import Path

import yaml

from metadata.datahub.tools.build_serving_governance_projection import build_projection

ROOT = Path(__file__).resolve().parents[1]


def _yaml(rel: str):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def test_serving_dataset_governance_preserves_metricflow_authority_and_agent_boundary():
    policy = _yaml("metadata/datahub/governance/serving_policy.yml")
    asset = policy["serving_assets"][0]
    assert policy["metric_authority"] == "metricflow"
    assert asset["dataset"]["expected_urn"] == (
        "urn:li:dataset:(urn:li:dataPlatform:iceberg,commerce_polaris.serving.bi_daily_executive,DEV)"
    )
    props = asset["structured_properties"]
    assert props["commerce.governance.metricAuthority"] == "METRICFLOW"
    assert props["commerce.governance.servingRole"] == "SHARED_BI_API_PROJECTION"
    assert props["commerce.governance.agentReadiness"] == "REFERENCE_ONLY"
    assert {"layer-serving", "metricflow-governed", "consumer-bi", "consumer-api"}.issubset(asset["tags"])


def test_serving_projection_models_datajob_dashboard_and_api_without_guessing_endpoint_urns():
    projection = build_projection()
    assert projection["runtime_verified"] is False
    item = projection["items"][0]
    job = item["export_job"]
    assert job["platform"] == "dagster"
    assert [x["model"] for x in job["upstream_datasets"]] == ["orders", "order_items", "refund_items"]
    assert job["downstream_dataset"] == item["dataset"]["expected_urn"]

    dashboard = item["consumers"]["dashboards"][0]
    assert dashboard["entity_type"] == "dashboard"
    assert dashboard["lineage"]["upstream"] == item["dataset"]["expected_urn"]

    endpoints = item["consumers"]["api_endpoints"]
    assert {x["path"] for x in endpoints} == {
        "/api/v1/executive/daily",
        "/api/v1/regions/{region}/daily",
    }
    assert all(x["expected_urn"] is None and x["resolved_urn"] is None for x in endpoints)
    assert projection["principles"]["api_endpoint_urn_guessing_forbidden"] is True


def test_fastapi_openapi_contract_and_datahub_recipe_cover_only_business_endpoints():
    spec = json.loads((ROOT / "serving/api/openapi.json").read_text(encoding="utf-8"))
    assert set(spec["paths"]) == {
        "/health/live",
        "/health/ready",
        "/api/v1/executive/daily",
        "/api/v1/regions/{region}/daily",
    }
    recipe = _yaml("metadata/datahub/recipes/serving_api_openapi.yml")
    cfg = recipe["source"]["config"]
    assert recipe["source"]["type"] == "openapi"
    assert cfg["enable_api_calls_for_schema_extraction"] is False
    assert set(cfg["ignore_endpoints"]) == {"/health/live", "/health/ready"}
    assert cfg["swagger_file"] == "serving/api/openapi.json"
    assert cfg["url"] == "http://localhost:8081"
    assert recipe["sink"]["config"]["server"] == "http://localhost:8080"


def test_runtime_integration_is_exact_identity_and_fail_closed():
    runtime = (ROOT / "metadata/datahub/tools/serving_runtime.py").read_text(encoding="utf-8")
    resolver = (ROOT / "metadata/datahub/tools/resolve_serving_consumer_identities.py").read_text(encoding="utf-8")
    assert "client.lineage.add_lineage(upstream=serving, downstream=urn)" in runtime
    assert "API endpoint identity evidence is missing" in runtime
    assert ".search(" not in runtime
    assert ".search(" not in resolver
    assert "graph.exists(urn)" in resolver
    assert 'spec["path"] in text' in resolver
    assert 'spec["method"].lower() in text.lower()' in resolver


def test_new_governance_definitions_are_registered_and_gates_default_false():
    tags = _yaml("metadata/datahub/governance/tags.yml")
    tag_ids = {x["id"] for x in tags["tags"]}
    assert {"layer-serving", "metricflow-governed", "consumer-bi", "consumer-api"}.issubset(tag_ids)

    props = _yaml("metadata/datahub/governance/structured_properties.yml")
    prop_ids = {x["id"] for x in props["properties"]}
    assert {"commerce.governance.metricAuthority", "commerce.governance.servingRole"}.issubset(prop_ids)

    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for gate in (
        "SERVING_GOVERNANCE_ALLOW_DATAHUB_WRITE",
        "SERVING_GOVERNANCE_ALLOW_LINEAGE_WRITE",
        "SERVING_GOVERNANCE_ALLOW_CONSUMER_WRITE",
    ):
        assert f"{gate}=false" in env


def test_architecture_source_shows_datahub_covering_serving_and_consumers():
    mermaid = (ROOT / "docs/architecture/AI_NATIVE_DATA_AGENT.mmd").read_text(encoding="utf-8")
    for token in ("Serving Dataset Governance", "Dashboard Lineage", "OpenAPI Endpoint Metadata"):
        assert token in mermaid
    assert (ROOT / "docs/SERVING_GOVERNANCE_AND_LINEAGE.md").exists()


def test_committed_openapi_contract_matches_current_fastapi_app():
    import json
    from serving.api.main import app

    committed = json.loads((ROOT / "serving/api/openapi.json").read_text(encoding="utf-8"))
    assert committed == app.openapi()
