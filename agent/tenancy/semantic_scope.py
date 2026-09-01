"""RequestContext -> Semantic Query 强制范围注入。

关键安全点：
- 只从可信 RequestContext 注入；
- 只允许 semantic_query_policy.yml 中已有的受治理维度；
- scope value 必须来自当前 canonical seed；
- 用户 Prompt 与 tenant scope 冲突时直接 BLOCKED；
- V1 不支持多值范围，不会偷偷降级成无过滤查询。
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agent.semantic_query.contracts import (
    SemanticDimensionFilter,
    SemanticFilterOperator,
    SemanticQueryPlan,
    SemanticQueryStatus,
)

from .contracts import RequestContext


class GovernedRequestScopeEnforcer:
    """把可信 tenant dimension scope 合并到 READY SemanticQueryPlan。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/tenant_runtime_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        self.semantic_policy = yaml.safe_load(
            (self.root / "agent/contracts/semantic_query_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        self._canonical_values = self._load_values()

    def apply(
        self,
        plan: SemanticQueryPlan,
        request_context: RequestContext | None,
    ) -> tuple[SemanticQueryPlan, str | None]:
        if request_context is None or not request_context.dimension_scopes:
            return plan, None
        if plan.status is not SemanticQueryStatus.READY or plan.spec is None:
            return plan, None

        max_scopes = int(self.policy["limits"]["max_dimension_scopes"])
        if len(request_context.dimension_scopes) > max_scopes:
            return plan, (
                f"RequestContext contains {len(request_context.dimension_scopes)} "
                f"dimension scopes; maximum is {max_scopes}."
            )

        filters = list(plan.spec.filters)
        for scope in request_context.dimension_scopes:
            if scope.dimension not in self.semantic_policy.get(
                "structured_filter_dimensions", {}
            ):
                return plan, (
                    f"Tenant scope dimension is outside governed Semantic dimensions: "
                    f"{scope.dimension}"
                )

            if len(scope.values) != 1:
                return plan, (
                    f"Tenant scope {scope.dimension} requires exactly one canonical value "
                    "in V1; multi-value scope needs governed IN semantics."
                )

            value = scope.values[0]
            if value not in self._canonical_values.get(scope.dimension, set()):
                return plan, (
                    f"Tenant scope value is not a current governed canonical value: "
                    f"{scope.dimension}={value}"
                )

            existing = [item for item in filters if item.dimension == scope.dimension]
            if existing:
                if any(item.value != value for item in existing):
                    return plan, (
                        f"User filter conflicts with mandatory tenant scope for "
                        f"{scope.dimension}."
                    )
                # 相同值已经存在时去重；RequestContext 仍是授权来源。
                continue

            filters.append(
                SemanticDimensionFilter(
                    dimension=scope.dimension,
                    operator=SemanticFilterOperator.EQ,
                    value=value,
                    source=f"tenant_scope:{request_context.tenant_id}",
                )
            )

        max_total = int(self.policy["limits"]["max_total_semantic_filters"])
        if len(filters) > max_total:
            return plan, (
                f"User filters + tenant scopes produce {len(filters)} Semantic filters; "
                f"maximum is {max_total}."
            )

        scoped_spec = replace(plan.spec, filters=tuple(filters))
        return replace(plan, spec=scoped_spec), None

    def _load_values(self) -> dict[str, set[str]]:
        output: dict[str, set[str]] = {}
        for dimension, config in self.semantic_policy.get(
            "structured_filter_dimensions", {}
        ).items():
            source = config.get("value_source") or {}
            if source.get("type") != "csv":
                output[dimension] = set()
                continue

            path = self.root / str(source["path"])
            column = str(source["column"])
            values: set[str] = set()
            if path.exists():
                with path.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        value = str(row.get(column, "") or "").strip()
                        if value:
                            values.add(value)
            output[dimension] = values
        return output
