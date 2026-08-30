from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from agent.router import DeterministicToolRouter, GovernedPlanExecutor
from agent.tools import GovernedMetadataTools

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(rel: str):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def test_capability_manifest_closes_phase5a_through_phase5h_file_contracts():
    manifest = load_yaml("agent/contracts/phase5_capability_manifest.yml")
    assert manifest["version"] == 1
    assert set(manifest["phases"]) == {"5A", "5B", "5C", "5D", "5E", "5F", "5G", "5H"}
    for phase, item in manifest["phases"].items():
        assert item["runtime_evidence"] == "DEFERRED", phase
        for rel in [item["policy"], *item["implementation"], *item["tests"], item["static_runner"], item["live_runner"]]:
            assert (ROOT / rel).exists(), f"{phase}: missing {rel}"
        assert os.access(ROOT / item["static_runner"], os.X_OK), phase
        assert os.access(ROOT / item["live_runner"], os.X_OK), phase


def test_phase5_limits_are_consistent_across_query_discovery_resolution_session_and_breakdown():
    query = load_yaml("agent/contracts/semantic_query_policy.yml")
    discovery = load_yaml("agent/contracts/dimension_value_policy.yml")
    resolution = load_yaml("agent/contracts/dimension_resolution_policy.yml")
    session = load_yaml("agent/contracts/analysis_session_policy.yml")
    breakdown = load_yaml("agent/contracts/comparative_breakdown_policy.yml")
    assert query["limits"]["max_metrics"] == discovery["limits"]["max_metrics"] == resolution["limits"]["max_metrics"] == session["limits"]["max_metrics"] == breakdown["limits"]["max_metrics"] == 3
    assert query["limits"]["max_filters"] == session["limits"]["max_filters"] == 2
    assert query["limits"]["max_time_range_days"] == discovery["limits"]["max_time_range_days"] == breakdown["limits"]["max_comparison_window_days"] == 366


def test_public_tool_schema_matches_executable_surface_and_exposes_no_sql_escape_hatch():
    schema = json.loads((ROOT / "agent/contracts/tool_schemas.json").read_text(encoding="utf-8"))
    tools = {item["name"]: item for item in schema["tools"]}
    expected = {
        "search_metadata",
        "get_entity_context",
        "get_metric_context",
        "get_dataset_context",
        "get_lineage_context",
        "get_runtime_context",
        "query_semantic_metric",
        "query_semantic_metrics",
        "get_dimension_values",
        "resolve_dimension_value",
    }
    assert set(tools) == expected
    methods = set(dir(GovernedMetadataTools))
    for name in expected - {"query_semantic_metric", "query_semantic_metrics"}:
        assert name in methods, f"Tool schema declares {name}, but no executable tool implementation exists"
    for name, spec in tools.items():
        props = set((spec.get("parameters") or {}).get("properties") or {})
        assert not props.intersection({"sql", "where", "predicate", "expression", "raw_where"}), name


def test_router_contract_exists_and_every_planned_tool_is_in_public_schema():
    routing = load_yaml("agent/contracts/intent_routing.yml")
    assert routing["limits"]["max_tool_calls"] == 3
    schema = json.loads((ROOT / "agent/contracts/tool_schemas.json").read_text(encoding="utf-8"))
    allowed = {item["name"] for item in schema["tools"]}
    router = DeterministicToolRouter(ROOT)
    questions = [
        "activity_net_sales 是什么意思？",
        "订单实体是什么？",
        "orders 属于哪个业务域，谁负责？",
        "orders 的上游血缘是什么？",
        "为什么 orders 昨天没更新？",
        "refund_rate 这个指标怎么算？",
        "2026-08-05 gross_sales 是多少？",
        "gross_sales 有哪些地区可以筛？",
    ]
    for question in questions:
        plan = router.plan(question)
        assert len(plan.steps) <= 3
        assert {step.tool for step in plan.steps}.issubset(allowed)


def test_phase4d_dependency_surface_is_actually_executable_not_only_documented():
    router = DeterministicToolRouter(ROOT)
    executor = GovernedPlanExecutor(ROOT)
    expected = {
        "activity_net_sales 是什么意思？": ("COMPLETE", "get_metric_context"),
        "订单实体是什么？": ("COMPLETE", "get_entity_context"),
        "orders 属于哪个业务域，谁负责？": ("COMPLETE", "get_dataset_context"),
        "orders 的上游血缘是什么？": ("COMPLETE", "get_lineage_context"),
        "为什么 orders 昨天没更新？": ("DEFERRED", "get_runtime_context"),
        "refund_rate 这个指标怎么算？": ("NEEDS_DISCOVERY", "search_metadata"),
    }
    for question, (status, final_tool) in expected.items():
        execution = executor.execute(router.plan(question))
        assert execution.status.value == status, question
        assert execution.results, question
        assert execution.results[-1]["tool"] == final_tool, question


def test_governance_source_files_referenced_by_generated_context_are_present():
    expected = {
        "domains.yml",
        "owners.yml",
        "tags.yml",
        "glossary.yml",
        "entity_registry.yml",
        "metric_registry.yml",
        "asset_policy.yml",
        "structured_properties.yml",
    }
    actual = {p.name for p in (ROOT / "metadata/datahub/governance").glob("*.yml")}
    assert expected.issubset(actual)
    identity = json.loads((ROOT / "metadata/datahub/generated/dataset_identity_resolution.json").read_text())
    assert identity["mode"] == "EXPECTED_ONLY"
    assert identity["runtime_verified"] is False
    assert all(item["status"] == "UNVERIFIED_EXPECTED" and item["resolved_urn"] is None for item in identity["identities"])


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def test_generated_agent_samples_do_not_point_to_missing_local_sources():
    missing = []
    for sample in sorted((ROOT / "agent/generated").glob("*.json")):
        payload = json.loads(sample.read_text(encoding="utf-8"))
        for obj in _walk(payload):
            for key in ("location", "source_file"):
                rel = obj.get(key)
                if not isinstance(rel, str) or "/" not in rel or rel.startswith(("urn:", "http://", "https://")):
                    continue
                if not (ROOT / rel).exists():
                    missing.append((sample.name, rel))
    assert missing == []


def test_all_live_runtime_gates_are_explicitly_false_in_env_example():
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    manifest = load_yaml("agent/contracts/phase5_capability_manifest.yml")
    gates = {gate for item in manifest["phases"].values() for gate in item["runtime_gates"]}
    for gate in gates:
        assert f"{gate}=false" in env_text, gate


def test_generated_samples_are_static_only_not_runtime_certification():
    for name in [
        "semantic_query_samples.json",
        "dimension_value_samples.json",
        "dimension_resolution_samples.json",
        "clarification_samples.json",
        "analysis_session_samples.json",
        "time_comparison_samples.json",
        "comparative_breakdown_samples.json",
    ]:
        text = (ROOT / "agent/generated" / name).read_text(encoding="utf-8")
        assert "real MetricFlow execution is DEFERRED" in text or "RUNTIME_VERIFIED" not in text


def test_shopify_source_contract_remains_source_aligned_and_complete():
    source = load_yaml("dbt/mercaso_dbt/models/sources/shopify.yml")
    shopify = source["sources"][0]
    assert shopify["name"] == "shopify"
    assert shopify["schema"] == "source"
    assert [item["name"] for item in shopify["tables"]] == [
        "orders",
        "order_items",
        "line_item_discount_allocations",
        "transactions",
        "refunds",
        "refund_items",
        "refund_transactions",
        "fulfillments",
        "fulfillment_items",
        "fulfillment_events",
    ]


def test_phase4_phase5_docs_do_not_reference_missing_local_artifacts():
    import re

    refs = set()
    docs = [*sorted((ROOT / "docs").glob("PHASE4*.md")), *sorted((ROOT / "docs").glob("PHASE5*.md")), ROOT / "agent/README.md"]
    pattern = re.compile(r"(?<![\w.-])((?:agent|metadata|infra|tests|dbt|orchestration)/[A-Za-z0-9_./-]+\.(?:py|yml|yaml|json|sh|md))")
    for path in docs:
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            refs.add(match.group(1).rstrip(").,`"))
    missing = sorted(rel for rel in refs if not (ROOT / rel).exists())
    assert missing == []
