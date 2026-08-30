from __future__ import annotations

from pathlib import Path
import shutil

import yaml

from agent.context.repository import GovernedContextRepository
from metadata.datahub.tools.validate_metric_lifecycle import validate_metric_lifecycle


ROOT = Path(__file__).resolve().parents[1]


def _copy_minimal_contract_tree(tmp_path: Path) -> Path:
    targets = [
        "metadata/datahub/governance/metric_registry.yml",
        "metadata/datahub/governance/metric_lifecycle.yml",
        "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml",
        "dbt/mercaso_dbt/models/metrics/lifecycle.yml",
        "dbt/mercaso_dbt/models/metrics/sales.yml",
    ]
    for rel in targets:
        src = ROOT / rel
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return tmp_path


def test_current_metric_version_lifecycle_contract_is_valid():
    assert validate_metric_lifecycle(ROOT) == []


def test_every_governed_metric_points_to_one_active_current_version():
    registry = yaml.safe_load((ROOT / "metadata/datahub/governance/metric_registry.yml").read_text(encoding="utf-8"))
    lifecycle = yaml.safe_load((ROOT / "metadata/datahub/governance/metric_lifecycle.yml").read_text(encoding="utf-8"))
    by_key = {(item["metric_id"], item["version"]): item for item in lifecycle["metric_versions"]}

    for metric in registry["metrics"]:
        current = by_key[(metric["id"], metric["current_version"])]
        assert current["status"] == "ACTIVE"
        assert current["definition_authority"] == "dbt_metricflow"
        assert current["definition_fingerprint"].startswith("sha256:")


def test_silent_metric_definition_rewrite_is_blocked_by_fingerprint(tmp_path: Path):
    root = _copy_minimal_contract_tree(tmp_path)
    semantic_path = root / "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml"
    semantic = yaml.safe_load(semantic_path.read_text(encoding="utf-8"))
    for model in semantic["models"]:
        for metric in model.get("metrics", []) or []:
            if metric["name"] == "gross_sales":
                metric["expr"] = "gross_sales_amount + 1"
    semantic_path.write_text(yaml.safe_dump(semantic, sort_keys=False, allow_unicode=True), encoding="utf-8")

    errors = validate_metric_lifecycle(root)
    assert any("gross_sales v1: definition fingerprint drift" in error for error in errors)


def test_post_baseline_version_requires_effective_from(tmp_path: Path):
    root = _copy_minimal_contract_tree(tmp_path)
    lifecycle_path = root / "metadata/datahub/governance/metric_lifecycle.yml"
    lifecycle = yaml.safe_load(lifecycle_path.read_text(encoding="utf-8"))
    v1 = next(item for item in lifecycle["metric_versions"] if item["metric_id"] == "average_order_value")
    v1["status"] = "DEPRECATED"
    v2 = dict(v1)
    v2.update(
        version=2,
        status="ACTIVE",
        change_type="BREAKING",
        effective_from=None,
        effective_to=None,
        supersedes_version=1,
    )
    lifecycle["metric_versions"].append(v2)
    lifecycle_path.write_text(yaml.safe_dump(lifecycle, sort_keys=False, allow_unicode=True), encoding="utf-8")

    registry_path = root / "metadata/datahub/governance/metric_registry.yml"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    next(item for item in registry["metrics"] if item["id"] == "average_order_value")["current_version"] = 2
    registry_path.write_text(yaml.safe_dump(registry, sort_keys=False, allow_unicode=True), encoding="utf-8")

    errors = validate_metric_lifecycle(root)
    assert any("average_order_value v2: effective_from is required" in error for error in errors)


def test_agent_metric_context_exposes_current_business_version_and_lifecycle():
    repository = GovernedContextRepository(ROOT)
    context = repository.metric_context("average_order_value")
    assert context is not None
    assert context["business_version"] == 1
    assert context["lifecycle_status"] == "ACTIVE"
    assert context["definition_fingerprint"].startswith("sha256:")
    assert context["lifecycle_source_of_truth"] == "metadata/datahub/governance/metric_lifecycle.yml"
