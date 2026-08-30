from __future__ import annotations

"""Metric Version Lifecycle（指标版本生命周期）静态校验器。

业务逻辑：
- MetricFlow / dbt 继续拥有指标公式；本模块只校验治理版本、状态和生效关系。
- 当前 registry 只暴露一个 current_version；历史版本保存在 append-only lifecycle ledger。
- 当前 ACTIVE 版本保存 definition fingerprint，阻止“公式改了但忘记升业务版本”的静默漂移。

工程边界：这里产生的是 Static Contract Evidence，不代表 MetricFlow Runtime 已执行。
"""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


ALLOWED_STATUS = {"DRAFT", "ACTIVE", "DEPRECATED", "RETIRED"}
ALLOWED_CHANGE_TYPE = {"BASELINE", "NON_BREAKING", "BREAKING"}
NON_SEMANTIC_KEYS = {"label", "description"}


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _canonicalize(value: Any) -> Any:
    """递归移除只影响展示的字段，并规范字典顺序供 SHA-256 指纹使用。"""
    if isinstance(value, dict):
        return {
            key: _canonicalize(item)
            for key, item in sorted(value.items())
            if key not in NON_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def metric_definitions(root: Path) -> dict[str, dict[str, Any]]:
    """读取 canonical dbt / MetricFlow 定义，返回 Metric ID -> 语义定义。"""
    definitions: dict[str, dict[str, Any]] = {}
    semantic_path = root / "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml"
    semantic = _load_yaml(semantic_path)
    for model in semantic.get("models", []) or []:
        for metric in model.get("metrics", []) or []:
            definitions[metric["name"]] = {
                "source_model": model["name"],
                "definition": metric,
            }

    metrics_dir = root / "dbt/mercaso_dbt/models/metrics"
    for path in sorted(metrics_dir.glob("*.yml")):
        payload = _load_yaml(path)
        for metric in payload.get("metrics", []) or []:
            definitions[metric["name"]] = {
                "source_model": None,
                "definition": metric,
            }
    return definitions


def definition_fingerprint(metric_name: str, definition: dict[str, Any]) -> str:
    """对会影响指标计算语义的 canonical 定义生成稳定 SHA-256。"""
    canonical = {
        "metric_id": metric_name,
        "source_model": definition.get("source_model"),
        "definition": _canonicalize(definition["definition"]),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def current_fingerprints(root: Path) -> dict[str, str]:
    return {
        name: definition_fingerprint(name, definition)
        for name, definition in metric_definitions(root).items()
    }


def validate_metric_lifecycle(root: Path) -> list[str]:
    """返回所有治理错误；空列表表示 Static Contract PASS。"""
    errors: list[str] = []
    registry_path = root / "metadata/datahub/governance/metric_registry.yml"
    lifecycle_path = root / "metadata/datahub/governance/metric_lifecycle.yml"
    registry = _load_yaml(registry_path)
    lifecycle = _load_yaml(lifecycle_path)
    definitions = metric_definitions(root)
    fingerprints = current_fingerprints(root)

    registry_rows = registry.get("metrics", []) or []
    lifecycle_rows = lifecycle.get("metric_versions", []) or []

    registry_ids: set[str] = set()
    for row in registry_rows:
        metric_id = row.get("id")
        if not metric_id:
            errors.append("metric_registry contains a row without id")
            continue
        if metric_id in registry_ids:
            errors.append(f"duplicate metric_registry id: {metric_id}")
        registry_ids.add(metric_id)
        version = row.get("current_version")
        if not isinstance(version, int) or version < 1:
            errors.append(f"{metric_id}: current_version must be an integer >= 1")
        if metric_id not in definitions:
            errors.append(f"{metric_id}: governed metric has no canonical dbt / MetricFlow definition")

    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for row in lifecycle_rows:
        metric_id = row.get("metric_id")
        version = row.get("version")
        if not metric_id or not isinstance(version, int):
            errors.append("metric_lifecycle row requires metric_id and integer version")
            continue
        key = (metric_id, version)
        if key in by_key:
            errors.append(f"duplicate lifecycle version: {metric_id} v{version}")
        by_key[key] = row
        by_metric.setdefault(metric_id, []).append(row)

        status = row.get("status")
        if status not in ALLOWED_STATUS:
            errors.append(f"{metric_id} v{version}: invalid status {status!r}")
        change_type = row.get("change_type")
        if change_type not in ALLOWED_CHANGE_TYPE:
            errors.append(f"{metric_id} v{version}: invalid change_type {change_type!r}")
        if version == 1 and change_type != "BASELINE":
            errors.append(f"{metric_id} v1: first governed version must use change_type BASELINE")
        if version > 1:
            if change_type == "BASELINE":
                errors.append(f"{metric_id} v{version}: later versions cannot use BASELINE")
            if not row.get("effective_from"):
                errors.append(f"{metric_id} v{version}: effective_from is required for post-baseline versions")
            supersedes = row.get("supersedes_version")
            if not isinstance(supersedes, int) or supersedes >= version:
                errors.append(f"{metric_id} v{version}: supersedes_version must reference an earlier version")
            elif (metric_id, supersedes) not in by_key and not any(
                x.get("metric_id") == metric_id and x.get("version") == supersedes for x in lifecycle_rows
            ):
                errors.append(f"{metric_id} v{version}: supersedes_version v{supersedes} does not exist")
        if status == "ACTIVE" and row.get("effective_to") is not None:
            errors.append(f"{metric_id} v{version}: ACTIVE version cannot have effective_to")
        if status == "RETIRED" and not row.get("effective_to"):
            errors.append(f"{metric_id} v{version}: RETIRED version requires effective_to")

    lifecycle_metric_ids = set(by_metric)
    missing_lifecycle = sorted(registry_ids - lifecycle_metric_ids)
    if missing_lifecycle:
        errors.append("governed metrics missing lifecycle history: " + ", ".join(missing_lifecycle))

    extra_lifecycle = sorted(lifecycle_metric_ids - registry_ids)
    if extra_lifecycle:
        errors.append("lifecycle metrics missing from current governed registry: " + ", ".join(extra_lifecycle))

    for registry_row in registry_rows:
        metric_id = registry_row.get("id")
        current_version = registry_row.get("current_version")
        if not metric_id or not isinstance(current_version, int):
            continue
        current = by_key.get((metric_id, current_version))
        if current is None:
            errors.append(f"{metric_id}: current_version v{current_version} missing from lifecycle ledger")
            continue
        if current.get("status") != "ACTIVE":
            errors.append(f"{metric_id} v{current_version}: registry current_version must point to ACTIVE lifecycle version")
        active = [row for row in by_metric.get(metric_id, []) if row.get("status") == "ACTIVE"]
        if len(active) != 1:
            errors.append(f"{metric_id}: expected exactly one ACTIVE version, found {len(active)}")
        expected_fp = fingerprints.get(metric_id)
        recorded_fp = current.get("definition_fingerprint")
        if expected_fp and recorded_fp != expected_fp:
            errors.append(
                f"{metric_id} v{current_version}: definition fingerprint drift; "
                "change the metric intentionally and create a new lifecycle version instead of silently rewriting the active version"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate governed Metric version lifecycle contracts.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--print-fingerprints", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.print_fingerprints:
        for metric, fingerprint in sorted(current_fingerprints(root).items()):
            print(f"{metric}: {fingerprint}")
        return 0
    errors = validate_metric_lifecycle(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    registry = _load_yaml(root / "metadata/datahub/governance/metric_registry.yml")
    print(f"Metric lifecycle contract validated: {len(registry.get('metrics', []))} governed metrics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
