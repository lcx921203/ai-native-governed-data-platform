from __future__ import annotations

import re
import runpy
from pathlib import Path
from typing import Any

import yaml


_REF_RE = re.compile(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)")
_SOURCE_RE = re.compile(r"source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)")


class GovernedContextRepository:
    """Read-only composition layer over Git/dbt owned contracts.

    This repository intentionally does not become a new source of truth. It reads dbt /
    MetricFlow semantics and Git governance definitions and returns bounded structures for
    governed Agent tools.
    """

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()

    def yaml(self, rel: str) -> dict[str, Any]:
        return yaml.safe_load((self.root / rel).read_text(encoding="utf-8"))

    def governed_metric_ids(self) -> tuple[str, ...]:
        data = self.yaml("metadata/datahub/governance/metric_registry.yml")
        return tuple(item["id"] for item in data["metrics"])

    def metric_registry(self) -> dict[str, dict[str, Any]]:
        data = self.yaml("metadata/datahub/governance/metric_registry.yml")
        return {item["id"]: item for item in data["metrics"]}

    def metric_lifecycle(self) -> dict[str, list[dict[str, Any]]]:
        """读取 Metric Version Lifecycle（指标版本生命周期）账本。

        返回：metric_id -> 按 version 升序排列的历史版本。
        工程边界：这里只读取治理元数据；指标公式仍由 dbt / MetricFlow 拥有。
        """
        data = self.yaml("metadata/datahub/governance/metric_lifecycle.yml")
        history: dict[str, list[dict[str, Any]]] = {}
        for item in data.get("metric_versions", []) or []:
            history.setdefault(item["metric_id"], []).append(item)
        for items in history.values():
            items.sort(key=lambda item: int(item["version"]))
        return history

    def current_metric_lifecycle(self, metric_name: str) -> dict[str, Any] | None:
        registry = self.metric_registry().get(metric_name)
        if registry is None:
            return None
        current_version = registry.get("current_version")
        for item in self.metric_lifecycle().get(metric_name, []):
            if item.get("version") == current_version:
                return item
        return None

    def glossary(self) -> dict[str, dict[str, Any]]:
        data = self.yaml("metadata/datahub/governance/glossary.yml")
        return {item["id"]: item for item in data["terms"]}

    def entity_registry(self) -> dict[str, dict[str, Any]]:
        data = self.yaml("metadata/datahub/governance/entity_registry.yml")
        return {item["id"]: item for item in data["entities"]}

    def asset_policy(self) -> dict[str, Any]:
        return self.yaml("metadata/datahub/governance/asset_policy.yml")

    def asset_policy_index(self) -> dict[str, dict[str, Any]]:
        return {item["model"]: item for item in self.asset_policy()["assets"]}

    def domains(self) -> dict[str, dict[str, Any]]:
        data = self.yaml("metadata/datahub/governance/domains.yml")
        items = [data["root"], *data.get("subdomains", [])]
        return {item["id"]: item for item in items}

    def owners(self) -> dict[str, dict[str, Any]]:
        data = self.yaml("metadata/datahub/governance/owners.yml")
        return {item["id"]: item for item in data["groups"]}

    def tags(self) -> dict[str, dict[str, Any]]:
        data = self.yaml("metadata/datahub/governance/tags.yml")
        return {item["id"]: item for item in data["tags"]}

    def structured_properties(self) -> dict[str, dict[str, Any]]:
        data = self.yaml("metadata/datahub/governance/structured_properties.yml")
        return {item["id"]: item for item in data["properties"]}

    def dataset_identities(self) -> dict[str, dict[str, Any]]:
        import json

        data = json.loads(
            (self.root / "metadata/datahub/generated/dataset_identity_resolution.json").read_text(
                encoding="utf-8"
            )
        )
        return {item["model"]: item for item in data["identities"]}

    def semantic_models(self) -> list[dict[str, Any]]:
        return self.yaml("dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml")["models"]

    def metric_definitions(self) -> dict[str, dict[str, Any]]:
        definitions: dict[str, dict[str, Any]] = {}
        for model in self.semantic_models():
            for metric in model.get("metrics", []) or []:
                definitions[metric["name"]] = {
                    **metric,
                    "source_model": model["name"],
                    "source_file": "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml",
                }
        metrics_dir = self.root / "dbt/mercaso_dbt/models/metrics"
        for path in sorted(metrics_dir.glob("*.yml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for metric in data.get("metrics", []) or []:
                definitions[metric["name"]] = {
                    **metric,
                    "source_model": None,
                    "source_file": str(path.relative_to(self.root)),
                }
        return definitions

    def metric_related_models(self, metric_name: str) -> tuple[str, ...]:
        defs = self.metric_definitions()
        seen: set[str] = set()
        models: list[str] = []

        def visit(name: str) -> None:
            if name in seen:
                return
            seen.add(name)
            item = defs.get(name)
            if not item:
                return
            if item.get("source_model"):
                model = str(item["source_model"])
                if model not in models:
                    models.append(model)
            for dep in item.get("input_metrics", []) or []:
                dep_name = dep.get("name") if isinstance(dep, dict) else str(dep)
                visit(dep_name)
            for key in ("numerator", "denominator"):
                dep = item.get(key)
                if dep:
                    dep_name = dep.get("name") if isinstance(dep, dict) else str(dep)
                    visit(dep_name)
            for key in ("base_metric", "conversion_metric"):
                dep = item.get(key)
                if dep:
                    dep_name = dep.get("name") if isinstance(dep, dict) else str(dep)
                    visit(dep_name)

        visit(metric_name)
        return tuple(models)

    def metric_context(self, metric_name: str) -> dict[str, Any] | None:
        registry = self.metric_registry().get(metric_name)
        definition = self.metric_definitions().get(metric_name)
        if registry is None:
            return None
        if definition is None:
            return {
                "id": metric_name,
                "status": "BROKEN_GOVERNANCE_REFERENCE",
                "glossary_term": registry.get("glossary_term"),
            }
        glossary = self.glossary().get(registry.get("glossary_term", ""), {})
        related_models = list(self.metric_related_models(metric_name))
        lifecycle = self.current_metric_lifecycle(metric_name) or {}
        return {
            "id": metric_name,
            "name": glossary.get("name") or definition.get("label") or metric_name,
            "description": glossary.get("description") or definition.get("description") or "",
            "definition": definition,
            "definition_source_of_truth": "dbt_metricflow",
            "related_models": related_models,
            "glossary_term": registry.get("glossary_term"),
            "business_version": registry.get("current_version"),
            "lifecycle_status": lifecycle.get("status"),
            "effective_from": lifecycle.get("effective_from"),
            "effective_to": lifecycle.get("effective_to"),
            "supersedes_version": lifecycle.get("supersedes_version"),
            "definition_fingerprint": lifecycle.get("definition_fingerprint"),
            "lifecycle_source_of_truth": "metadata/datahub/governance/metric_lifecycle.yml",
        }

    def entity_context(self, entity_name: str) -> dict[str, Any] | None:
        registry = self.entity_registry().get(entity_name)
        if registry is None:
            return None
        appearances: list[dict[str, Any]] = []
        for model in self.semantic_models():
            for column in model.get("columns", []) or []:
                entity = column.get("entity") or {}
                if entity.get("name") != entity_name:
                    continue
                dimensions = [
                    c["name"]
                    for c in model.get("columns", []) or []
                    if c.get("dimension")
                ]
                appearances.append(
                    {
                        "semantic_model": model["name"],
                        "column": column["name"],
                        "role": entity.get("type"),
                        "dimensions": dimensions if entity.get("type") == "primary" else [],
                    }
                )
        glossary = self.glossary().get(registry.get("glossary_term", ""), {})
        return {
            "entity": entity_name,
            "name": glossary.get("name") or entity_name,
            "description": glossary.get("description") or "",
            "semantic_model": registry.get("semantic_model"),
            "primary_models": [x["semantic_model"] for x in appearances if x["role"] == "primary"],
            "referenced_by_models": [x["semantic_model"] for x in appearances if x["role"] == "foreign"],
            "semantic_appearances": appearances,
            "glossary_term": registry.get("glossary_term"),
            "relationship_source_of_truth": "dbt_metricflow",
        }

    def dataset_context(self, model: str) -> dict[str, Any] | None:
        policy = self.asset_policy()
        asset = self.asset_policy_index().get(model)
        identity = self.dataset_identities().get(model)
        if asset is None or identity is None:
            return None
        defaults = policy["defaults"]
        domain = self.domains().get(asset["domain"], {})
        owners = self.owners()
        owner_ids = defaults.get("owners", {})
        tags = list(dict.fromkeys([*defaults.get("tags", []), *asset.get("tags", [])]))
        properties = dict(defaults.get("structured_properties", {}))
        properties.update(asset.get("structured_properties", {}))
        return {
            "model": model,
            "identity": identity,
            "domain": domain,
            "owners": {
                "business": owners.get(owner_ids.get("business"), {}),
                "technical": owners.get(owner_ids.get("technical"), {}),
            },
            "tags": [self.tags().get(tag, {"id": tag}) for tag in tags],
            "glossary_terms": [self.glossary().get(term, {"id": term}) | {"id": term} for term in asset.get("glossary_terms", [])],
            "structured_properties": properties,
            "runtime_dataset": identity.get("resolved_urn"),
        }

    def model_sql_path(self, model: str) -> Path | None:
        matches = list((self.root / "dbt/mercaso_dbt/models").rglob(f"{model}.sql"))
        return matches[0] if len(matches) == 1 else None

    def static_lineage(self, model: str, *, direction: str = "upstream", max_hops: int = 2) -> dict[str, Any]:
        if direction not in {"upstream", "downstream"}:
            raise ValueError("direction must be upstream or downstream")
        if not 1 <= max_hops <= 2:
            raise ValueError("max_hops must be between 1 and 2")
        if direction == "downstream":
            edges: dict[str, list[str]] = {}
            for sql in (self.root / "dbt/mercaso_dbt/models").rglob("*.sql"):
                text = sql.read_text(encoding="utf-8")
                child = sql.stem
                for parent in _REF_RE.findall(text):
                    edges.setdefault(parent, []).append(child)
            frontier = [model]
            result: list[dict[str, Any]] = []
            seen = {model}
            for hop in range(1, max_hops + 1):
                next_frontier: list[str] = []
                for node in frontier:
                    for child in sorted(set(edges.get(node, []))):
                        if child in seen:
                            continue
                        seen.add(child)
                        result.append({"from": node, "to": child, "hop": hop})
                        next_frontier.append(child)
                frontier = next_frontier
            return {"model": model, "direction": direction, "max_hops": max_hops, "edges": result}

        result: list[dict[str, Any]] = []
        frontier = [model]
        seen = {model}
        for hop in range(1, max_hops + 1):
            next_frontier: list[str] = []
            for node in frontier:
                path = self.model_sql_path(node)
                if path is None:
                    continue
                text = path.read_text(encoding="utf-8")
                parents = list(dict.fromkeys(_REF_RE.findall(text)))
                source_parents = [f"source:{a}.{b}" for a, b in _SOURCE_RE.findall(text)]
                for parent in [*parents, *source_parents]:
                    if parent in seen:
                        continue
                    seen.add(parent)
                    result.append({"from": node, "to": parent, "hop": hop})
                    if not parent.startswith("source:"):
                        next_frontier.append(parent)
            frontier = next_frontier
        return {"model": model, "direction": direction, "max_hops": max_hops, "edges": result}

    def automation_contract(self, dataset: str) -> dict[str, Any]:
        """读取当前数据集的调度时间契约与九张 Mart SLA 归属。

        Phase 6 的 ``automation_policy.py`` 继续提供冻结的 Job / Schedule / Deadline 时间常量；
        post-baseline 的 ``consumer_sla.py`` 提供当前受治理消费者 Mart 列表。这样新增
        ``order_lifecycle_snapshot`` 后，Agent 能看到它已进入 Freshness / Recovery SLA，
        同时不改写历史 Phase 6 闭包。
        """

        module = runpy.run_path(
            str(self.root / "orchestration/dagster/commerce_dagster/automation_policy.py")
        )
        sla_module = runpy.run_path(
            str(self.root / "orchestration/dagster/commerce_dagster/consumer_sla.py")
        )
        daily_assets = tuple(sla_module["SHOPIFY_DAILY_MART_ASSET_KEYS"])
        return {
            "dataset": dataset,
            "daily_managed": dataset in daily_assets,
            "job": module["SHOPIFY_DAILY_JOB_NAME"],
            "timezone": module["SHOPIFY_AUTOMATION_TIMEZONE"],
            "schedule": f"{module['SHOPIFY_DAILY_SCHEDULE_HOUR']:02d}:{module['SHOPIFY_DAILY_SCHEDULE_MINUTE']:02d} UTC",
            "freshness_deadline": f"{module['SHOPIFY_DAILY_FRESHNESS_DEADLINE_HOUR']:02d}:{module['SHOPIFY_DAILY_FRESHNESS_DEADLINE_MINUTE']:02d} UTC",
            "freshness_budget_minutes": module["freshness_budget_minutes"](),
            "recovery_sensor_min_interval_seconds": module["SHOPIFY_RECOVERY_SENSOR_MIN_INTERVAL_SECONDS"],
            "recovery_horizon_days": module["SHOPIFY_RECOVERY_HORIZON_DAYS"],
        }
