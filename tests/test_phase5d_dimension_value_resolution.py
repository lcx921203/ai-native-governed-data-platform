from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.dimension_resolution import (
    DimensionResolutionMode,
    DimensionResolutionStatus,
    GovernedDimensionValueResolver,
)
from agent.semantic_query import GovernedSemanticQueryPlanner, SemanticQueryStatus
from agent.tools.governed_metadata import GovernedMetadataTools


ROOT = Path(__file__).resolve().parents[1]


def resolver() -> GovernedDimensionValueResolver:
    return GovernedDimensionValueResolver(ROOT)


def planner() -> GovernedSemanticQueryPlanner:
    return GovernedSemanticQueryPlanner(ROOT)


def test_phase5d_contract_is_fail_closed_and_fuzzy_is_candidate_only() -> None:
    policy = yaml.safe_load((ROOT / "agent/contracts/dimension_resolution_policy.yml").read_text())
    assert policy["version"] == 1
    assert policy["principles"]["metric_context_required"] is True
    assert policy["principles"]["exact_unique_match_may_auto_resolve"] is True
    assert policy["principles"]["fuzzy_match_is_candidate_only"] is True
    assert policy["principles"]["ambiguous_match_fail_closed"] is True
    assert policy["principles"]["unresolved_filter_must_not_be_dropped"] is True
    assert policy["principles"]["arbitrary_sql"] is False


def test_exact_value_can_infer_unique_dimension_from_static_value_universe() -> None:
    result = resolver().resolve(
        metrics=["gross_sales"],
        raw_value="South",
        question="2026-08-05 只看 South 的 gross_sales",
    )
    assert result.status is DimensionResolutionStatus.RESOLVED
    assert result.resolved_dimension == "store__region"
    assert result.resolved_value == "South"
    assert result.mode is DimensionResolutionMode.CANONICAL_EXACT
    assert result.evidence == "STATIC_CONTRACT"
    assert result.source_mode == "STATIC_SEED_FALLBACK"


def test_normalized_and_alias_exact_matches_resolve_to_canonical_value() -> None:
    normalized = resolver().resolve(
        metrics=["gross_sales"], raw_value="coca cola", dimension_hint="item__brand"
    )
    assert normalized.status is DimensionResolutionStatus.RESOLVED
    assert normalized.resolved_value == "Coca-Cola"
    assert normalized.mode is DimensionResolutionMode.NORMALIZED_EXACT

    alias = resolver().resolve(
        metrics=["gross_sales"], raw_value="可口可乐", dimension_hint="item__brand"
    )
    assert alias.status is DimensionResolutionStatus.RESOLVED
    assert alias.resolved_value == "Coca-Cola"
    assert alias.mode is DimensionResolutionMode.ALIAS_EXACT


def test_fuzzy_candidate_never_auto_becomes_filter() -> None:
    result = resolver().resolve(
        metrics=["gross_sales"], raw_value="Coca Colaa", dimension_hint="item__brand"
    )
    assert result.status is DimensionResolutionStatus.CLARIFICATION_REQUIRED
    assert result.resolved_value is None
    assert result.mode is DimensionResolutionMode.FUZZY_CANDIDATE
    assert result.candidates[0].value == "Coca-Cola"
    assert result.candidates[0].score > 0.9
    assert "never auto-applies" in result.warnings[0]


def test_unknown_value_is_not_found_and_never_silently_dropped() -> None:
    result = resolver().resolve(
        metrics=["gross_sales"], raw_value="Pepsi", dimension_hint="item__brand"
    )
    assert result.status is DimensionResolutionStatus.NOT_FOUND
    assert result.resolved_dimension is None
    assert result.resolved_value is None
    assert "Do not drop" in result.warnings[0]


def test_ambiguous_exact_value_requires_dimension_clarification(monkeypatch: pytest.MonkeyPatch) -> None:
    item = resolver()

    def fake_discover_values(*, metrics, dimension, question):
        if dimension in {"store__region", "item__brand"}:
            return ["Shared"], "RUNTIME_VERIFIED", "METRICFLOW_RUNTIME", []
        return [], "RUNTIME_VERIFIED", "METRICFLOW_RUNTIME", []

    monkeypatch.setattr(item, "_discover_values", fake_discover_values)
    result = item.resolve(metrics=["gross_sales"], raw_value="Shared")
    assert result.status is DimensionResolutionStatus.CLARIFICATION_REQUIRED
    assert result.resolved_value is None
    assert {(c.dimension, c.value) for c in result.candidates} == {
        ("store__region", "Shared"),
        ("item__brand", "Shared"),
    }


def test_runtime_discovery_can_resolve_new_value_not_present_in_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    item = resolver()

    def fake_discover_values(*, metrics, dimension, question):
        assert dimension == "item__brand"
        return ["Coca-Cola", "Generic", "Pepsi"], "RUNTIME_VERIFIED", "METRICFLOW_RUNTIME", []

    monkeypatch.setattr(item, "_discover_values", fake_discover_values)
    result = item.resolve(
        metrics=["gross_sales"], raw_value="Pepsi", dimension_hint="item__brand"
    )
    assert result.status is DimensionResolutionStatus.RESOLVED
    assert result.resolved_dimension == "item__brand"
    assert result.resolved_value == "Pepsi"
    assert result.evidence == "RUNTIME_VERIFIED"
    assert result.source_mode == "METRICFLOW_RUNTIME"
    # New Runtime values are not written into the static alias vocabulary.
    policy = yaml.safe_load((ROOT / "agent/contracts/semantic_query_policy.yml").read_text())
    assert "Pepsi" not in policy["structured_filter_dimensions"]["item__brand"]["value_aliases"]


def test_semantic_query_uses_runtime_resolved_dynamic_value_before_metricflow(monkeypatch: pytest.MonkeyPatch) -> None:
    item = planner()

    def fake_discover_values(*, metrics, dimension, question):
        if dimension == "item__brand":
            return ["Pepsi"], "RUNTIME_VERIFIED", "METRICFLOW_RUNTIME", []
        return [], "RUNTIME_VERIFIED", "METRICFLOW_RUNTIME", []

    monkeypatch.setattr(item.value_resolver, "_discover_values", fake_discover_values)
    plan = item.plan(
        metric="gross_sales",
        question="2026-08-05 品牌为 Pepsi 的 gross_sales 是多少？",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.spec is not None
    assert len(plan.spec.filters) == 1
    filt = plan.spec.filters[0]
    assert filt.dimension == "item__brand"
    assert filt.value == "Pepsi"
    assert filt.source == "dimension_value_resolution:CANONICAL_EXACT:RUNTIME_VERIFIED"
    assert "{{ Dimension('item__brand') }} = 'Pepsi'" in plan.command_preview


def test_semantic_query_can_infer_dimension_only_for_unique_exact_dynamic_match(monkeypatch: pytest.MonkeyPatch) -> None:
    item = planner()

    def fake_discover_values(*, metrics, dimension, question):
        if dimension == "item__brand":
            return ["Pepsi"], "RUNTIME_VERIFIED", "METRICFLOW_RUNTIME", []
        return [], "RUNTIME_VERIFIED", "METRICFLOW_RUNTIME", []

    monkeypatch.setattr(item.value_resolver, "_discover_values", fake_discover_values)
    plan = item.plan(
        metric="gross_sales",
        question="2026-08-05 只看 Pepsi 的 gross_sales 是多少？",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.spec is not None
    assert plan.spec.filters[0].dimension == "item__brand"
    assert plan.spec.filters[0].value == "Pepsi"


def test_semantic_query_fuzzy_dynamic_literal_requires_clarification() -> None:
    plan = planner().plan(
        metric="gross_sales",
        question="2026-08-05 品牌为 Coca Colaa 的 gross_sales 是多少？",
    )
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert plan.spec is None
    assert "item__brand=Coca-Cola" in plan.warnings[0]


def test_semantic_query_unknown_dynamic_literal_cannot_fall_back_to_unfiltered_query() -> None:
    plan = planner().plan(
        metric="gross_sales",
        question="2026-08-05 只看 Pepsi 的 gross_sales 是多少？",
    )
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert plan.spec is None
    assert "Pepsi" in plan.warnings[0]


def test_short_state_alias_does_not_match_inside_brand_text() -> None:
    plan = planner().plan(
        metric="gross_sales",
        question="2026-08-05 品牌为 coca cola 的 gross_sales 是多少？",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.spec is not None
    assert [(f.dimension, f.value) for f in plan.spec.filters] == [("item__brand", "Coca-Cola")]
    assert all(f.dimension != "store__state" for f in plan.spec.filters)


def test_public_resolution_tool_is_bounded_and_has_no_sql_where_surface() -> None:
    schemas = json.loads((ROOT / "agent/contracts/tool_schemas.json").read_text())
    tool = next(item for item in schemas["tools"] if item["name"] == "resolve_dimension_value")
    props = tool["parameters"]["properties"]
    assert props["metrics"]["maxItems"] == 3
    assert props["raw_value"]["maxLength"] == 128
    assert "sql" not in props
    assert "where" not in props
    assert set(x for x in props["dimension"]["enum"] if x is not None) == set(resolver().governed_dimensions)


def test_governed_tool_surface_exposes_resolution_without_mutation() -> None:
    result = GovernedMetadataTools(ROOT).resolve_dimension_value(
        metrics=["gross_sales"], raw_value="South"
    )
    assert result["tool"] == "resolve_dimension_value"
    assert result["status"] == "RESOLVED"
    assert result["payload"]["resolved_dimension"] == "store__region"
    assert result["payload"]["resolved_value"] == "South"
