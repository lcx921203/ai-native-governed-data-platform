"""Semantic Context Repository 的进程级只读 Snapshot Cache。

性能问题背景：
- ``metric_context()`` 原实现每次请求都会重复读取/解析多个 YAML；
- 一个 Metric Context 内还会再次调用 ``metric_definitions()``、
  ``metric_registry()``、``metric_lifecycle()``；
- Context Loader 与 Metadata Tool 各自持有一个 Repository 实例，
  因此同一请求会重复做静态治理文件解析。

生产语义：
- Git/dbt/MetricFlow 文件仍然是唯一 Source of Truth；
- Cache 只缓存部署版本内的静态 Semantic/Governance Contract；
- 不缓存 Prompt、Answer、Tenant、Subject、JWT、Runtime Result；
- Snapshot 在 Agent Runtime Readiness 之前预热；
- Contract 版本变化通过新部署/进程重启生效，不在请求路径做文件轮询；
- 多个 Repository 实例共享同一个进程 Snapshot。

这里通过继承原 ``repository.GovernedContextRepository`` 保持所有非 Semantic
能力原样，只覆盖 Metric/Semantic 热路径。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any

import yaml

from .repository import GovernedContextRepository as _BaseGovernedContextRepository


_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class _SemanticSnapshot:
    """一次部署版本内的静态 Semantic/Governance 快照。"""

    metric_ids: tuple[str, ...]
    metric_registry: dict[str, dict[str, Any]]
    metric_lifecycle: dict[str, list[dict[str, Any]]]
    glossary: dict[str, dict[str, Any]]
    semantic_models: list[dict[str, Any]]
    metric_definitions: dict[str, dict[str, Any]]
    related_models: dict[str, tuple[str, ...]]
    metric_contexts: dict[str, dict[str, Any]]
    build_duration_ms: float


def _related_models(
    definitions: dict[str, dict[str, Any]],
    metric_name: str,
) -> tuple[str, ...]:
    """按原 Repository 规则解析派生 Metric 的底层 Semantic Model。"""

    seen: set[str] = set()
    models: list[str] = []

    def visit(name: str) -> None:
        """递归解析 input/numerator/denominator/conversion 依赖。"""

        if name in seen:
            return
        seen.add(name)

        item = definitions.get(name)
        if not item:
            return

        if item.get("source_model"):
            model = str(item["source_model"])
            if model not in models:
                models.append(model)

        for dep in item.get("input_metrics", []) or []:
            dep_name = (
                dep.get("name")
                if isinstance(dep, dict)
                else str(dep)
            )
            visit(str(dep_name))

        for key in ("numerator", "denominator"):
            dep = item.get(key)
            if dep:
                dep_name = (
                    dep.get("name")
                    if isinstance(dep, dict)
                    else str(dep)
                )
                visit(str(dep_name))

        for key in ("base_metric", "conversion_metric"):
            dep = item.get(key)
            if dep:
                dep_name = (
                    dep.get("name")
                    if isinstance(dep, dict)
                    else str(dep)
                )
                visit(str(dep_name))

    visit(metric_name)
    return tuple(models)


def _build_semantic_snapshot(root_text: str) -> _SemanticSnapshot:
    """一次性读取 dbt/MetricFlow + Governance 静态来源并构造 Metric Context Index。"""

    started = perf_counter()
    root = Path(root_text).resolve()

    # 复用原 Repository 读取逻辑，避免新 Cache 变成第二套 Truth。
    base = _BaseGovernedContextRepository(root)

    registry = base.metric_registry()
    lifecycle = base.metric_lifecycle()
    glossary = base.glossary()
    semantic_models = base.semantic_models()

    # metric_definitions() 原实现会再次解析 semantic models。
    # Snapshot Builder 已经拥有 semantic_models，因此这里直接按相同规则组装，
    # 避免启动阶段也做无意义的第二次 YAML Parse。
    definitions: dict[str, dict[str, Any]] = {}
    semantic_source = (
        "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml"
    )
    for model in semantic_models:
        for metric in model.get("metrics", []) or []:
            definitions[metric["name"]] = {
                **metric,
                "source_model": model["name"],
                "source_file": semantic_source,
            }

    metrics_dir = root / "dbt/mercaso_dbt/models/metrics"
    for path in sorted(metrics_dir.glob("*.yml")):
        data = yaml.safe_load(
            path.read_text(encoding="utf-8")
        ) or {}
        for metric in data.get("metrics", []) or []:
            definitions[metric["name"]] = {
                **metric,
                "source_model": None,
                "source_file": str(path.relative_to(root)),
            }

    related = {
        metric_name: _related_models(
            definitions,
            metric_name,
        )
        for metric_name in definitions
    }

    current_lifecycle: dict[str, dict[str, Any]] = {}
    for metric_name, registry_item in registry.items():
        current_version = registry_item.get("current_version")
        for item in lifecycle.get(metric_name, []) or []:
            if item.get("version") == current_version:
                current_lifecycle[metric_name] = item
                break

    metric_contexts: dict[str, dict[str, Any]] = {}
    for metric_name, registry_item in registry.items():
        definition = definitions.get(metric_name)

        if definition is None:
            metric_contexts[metric_name] = {
                "id": metric_name,
                "status": "BROKEN_GOVERNANCE_REFERENCE",
                "glossary_term": registry_item.get(
                    "glossary_term"
                ),
            }
            continue

        glossary_item = glossary.get(
            registry_item.get("glossary_term", ""),
            {},
        )
        lifecycle_item = current_lifecycle.get(
            metric_name,
            {},
        )

        metric_contexts[metric_name] = {
            "id": metric_name,
            "name": (
                glossary_item.get("name")
                or definition.get("label")
                or metric_name
            ),
            "description": (
                glossary_item.get("description")
                or definition.get("description")
                or ""
            ),
            "definition": definition,
            "definition_source_of_truth": "dbt_metricflow",
            "related_models": list(
                related.get(metric_name, ())
            ),
            "glossary_term": registry_item.get(
                "glossary_term"
            ),
            "business_version": registry_item.get(
                "current_version"
            ),
            "lifecycle_status": lifecycle_item.get(
                "status"
            ),
            "effective_from": lifecycle_item.get(
                "effective_from"
            ),
            "effective_to": lifecycle_item.get(
                "effective_to"
            ),
            "supersedes_version": lifecycle_item.get(
                "supersedes_version"
            ),
            "definition_fingerprint": lifecycle_item.get(
                "definition_fingerprint"
            ),
            "lifecycle_source_of_truth": (
                "metadata/datahub/governance/metric_lifecycle.yml"
            ),
        }

    return _SemanticSnapshot(
        metric_ids=tuple(registry),
        metric_registry=registry,
        metric_lifecycle=lifecycle,
        glossary=glossary,
        semantic_models=semantic_models,
        metric_definitions=definitions,
        related_models=related,
        metric_contexts=metric_contexts,
        build_duration_ms=max(
            0.0,
            (perf_counter() - started) * 1000,
        ),
    )


@lru_cache(maxsize=8)
def _cached_semantic_snapshot(
    root_text: str,
) -> _SemanticSnapshot:
    """按 Repo Root 复用一次构建好的 Semantic Snapshot。"""

    return _build_semantic_snapshot(root_text)


def _snapshot_for_root(
    root: Path,
) -> _SemanticSnapshot:
    """串行化首次 Build，避免并发 Cold Start 重复解析同一批 YAML。"""

    with _CACHE_LOCK:
        return _cached_semantic_snapshot(
            str(root.resolve())
        )


def semantic_snapshot_cache_info() -> dict[str, int | None]:
    """返回非敏感 Cache 统计，供测试/诊断使用。"""

    info = _cached_semantic_snapshot.cache_info()
    return {
        "hits": info.hits,
        "misses": info.misses,
        "maxsize": info.maxsize,
        "currsize": info.currsize,
    }


def clear_semantic_snapshot_process_cache() -> None:
    """清空进程 Snapshot；仅供测试/本地显式重载，不在请求路径自动调用。"""

    with _CACHE_LOCK:
        _cached_semantic_snapshot.cache_clear()


class GovernedContextRepository(
    _BaseGovernedContextRepository
):
    """带进程级 Semantic Snapshot 的生产 Repository。

    非 Semantic 方法全部继承原 Repository；
    Metric/Semantic 热路径只从部署时静态 Snapshot 读取。
    """

    def __init__(
        self,
        project_root: Path | str,
    ):
        super().__init__(project_root)
        self._semantic_snapshot_ref: (
            _SemanticSnapshot | None
        ) = None

    def warm_semantic_snapshot(
        self,
    ) -> dict[str, int | float | str]:
        """在 Readiness 前预热 Snapshot，并返回非敏感构建摘要。"""

        snapshot = self._snapshot()
        return {
            "mode": "process_scoped_immutable_snapshot",
            "metric_count": len(
                snapshot.metric_registry
            ),
            "definition_count": len(
                snapshot.metric_definitions
            ),
            "semantic_model_count": len(
                snapshot.semantic_models
            ),
            "build_duration_ms": round(
                snapshot.build_duration_ms,
                3,
            ),
        }

    def _snapshot(self) -> _SemanticSnapshot:
        """返回当前实例绑定的共享 Snapshot；首次访问时绑定一次。"""

        if self._semantic_snapshot_ref is None:
            self._semantic_snapshot_ref = (
                _snapshot_for_root(self.root)
            )
        return self._semantic_snapshot_ref

    def governed_metric_ids(self) -> tuple[str, ...]:
        """直接返回 Snapshot 中受治理 Metric ID 顺序。"""

        return self._snapshot().metric_ids

    def metric_registry(
        self,
    ) -> dict[str, dict[str, Any]]:
        """返回隔离副本，防止调用方修改共享 Snapshot。"""

        return deepcopy(
            self._snapshot().metric_registry
        )

    def metric_lifecycle(
        self,
    ) -> dict[str, list[dict[str, Any]]]:
        """返回 Metric Lifecycle 的隔离副本。"""

        return deepcopy(
            self._snapshot().metric_lifecycle
        )

    def current_metric_lifecycle(
        self,
        metric_name: str,
    ) -> dict[str, Any] | None:
        """从 Snapshot 中读取当前业务版本 Lifecycle。"""

        snapshot = self._snapshot()
        registry = snapshot.metric_registry.get(
            metric_name
        )
        if registry is None:
            return None

        current_version = registry.get(
            "current_version"
        )
        for item in snapshot.metric_lifecycle.get(
            metric_name,
            [],
        ):
            if item.get("version") == current_version:
                return deepcopy(item)
        return None

    def glossary(
        self,
    ) -> dict[str, dict[str, Any]]:
        """返回共享 Governance Glossary 的隔离副本。"""

        return deepcopy(
            self._snapshot().glossary
        )

    def semantic_models(
        self,
    ) -> list[dict[str, Any]]:
        """返回 Semantic Model Snapshot 的隔离副本。"""

        return deepcopy(
            self._snapshot().semantic_models
        )

    def metric_definitions(
        self,
    ) -> dict[str, dict[str, Any]]:
        """返回一次构建好的 Metric Definition Index。"""

        return deepcopy(
            self._snapshot().metric_definitions
        )

    def metric_related_models(
        self,
        metric_name: str,
    ) -> tuple[str, ...]:
        """O(1) 返回预计算的 Metric -> Semantic Model 依赖。"""

        return self._snapshot().related_models.get(
            metric_name,
            (),
        )

    def metric_context(
        self,
        metric_name: str,
    ) -> dict[str, Any] | None:
        """O(1) 查找预组合 Metric Context，并返回隔离副本。"""

        context = self._snapshot().metric_contexts.get(
            metric_name
        )
        return (
            deepcopy(context)
            if context is not None
            else None
        )
