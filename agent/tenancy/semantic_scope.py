"""RequestContext -> Semantic Query 强制范围注入与对象范围校验。

关键安全点：
- 只从可信 RequestContext 注入；
- Metric / Business Dimension 必须落在 RequestContext Allowlist；
- ``metric_time__*`` 属于时间展示粒度，不当成业务对象权限；
- 只允许 semantic_query_policy.yml 中已有的受治理维度；
- scope value 必须来自当前 canonical seed；
- 用户 Prompt 与 tenant scope 冲突时直接 BLOCKED；
- V1 不支持多值范围，不会偷偷降级成无过滤查询。
"""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import yaml

from agent.semantic_query.contracts import (
    SemanticDimensionFilter,
    SemanticFilterOperator,
    SemanticQueryPlan,
    SemanticQueryStatus,
)

from .contracts import RequestContext


class GovernedRequestScopeEnforcer:
    """把可信对象权限和 tenant dimension scope 合并到 READY SemanticQueryPlan。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (
                self.root
                / "agent/contracts/tenant_runtime_policy.yml"
            ).read_text(encoding="utf-8")
        )
        self.semantic_policy = yaml.safe_load(
            (
                self.root
                / "agent/contracts/semantic_query_policy.yml"
            ).read_text(encoding="utf-8")
        )
        self._canonical_values = self._load_values()

    def apply(
        self,
        plan: SemanticQueryPlan,
        request_context: RequestContext | None,
    ) -> tuple[SemanticQueryPlan, str | None]:
        """先校验 Metric/Dimension Allowlist，再注入强制 Tenant Scope。"""

        if request_context is None:
            return plan, None
        if (
            plan.status is not SemanticQueryStatus.READY
            or plan.spec is None
        ):
            return plan, None

        object_warning = self._object_scope_warning(
            plan,
            request_context,
        )
        if object_warning:
            return plan, object_warning

        if not request_context.dimension_scopes:
            return plan, None

        max_scopes = int(
            self.policy["limits"]["max_dimension_scopes"]
        )
        if len(request_context.dimension_scopes) > max_scopes:
            return plan, (
                f"RequestContext contains {len(request_context.dimension_scopes)} "
                f"dimension scopes; maximum is {max_scopes}."
            )

        filters = list(plan.spec.filters)
        for scope in request_context.dimension_scopes:
            if scope.dimension not in self.semantic_policy.get(
                "structured_filter_dimensions",
                {},
            ):
                return plan, (
                    "Tenant scope dimension is outside governed "
                    f"Semantic dimensions: {scope.dimension}"
                )

            if len(scope.values) != 1:
                return plan, (
                    f"Tenant scope {scope.dimension} requires exactly one canonical value "
                    "in V1; multi-value scope needs governed IN semantics."
                )

            value = scope.values[0]
            if value not in self._canonical_values.get(
                scope.dimension,
                set(),
            ):
                return plan, (
                    "Tenant scope value is not a current governed "
                    f"canonical value: {scope.dimension}={value}"
                )

            existing = [
                item
                for item in filters
                if item.dimension == scope.dimension
            ]
            if existing:
                if any(
                    item.value != value
                    for item in existing
                ):
                    return plan, (
                        "User filter conflicts with mandatory tenant "
                        f"scope for {scope.dimension}."
                    )
                # 相同值已经存在时去重；RequestContext 仍是授权来源。
                continue

            filters.append(
                SemanticDimensionFilter(
                    dimension=scope.dimension,
                    operator=SemanticFilterOperator.EQ,
                    value=value,
                    source=(
                        f"tenant_scope:{request_context.tenant_id}"
                    ),
                )
            )

        max_total = int(
            self.policy["limits"]["max_total_semantic_filters"]
        )
        if len(filters) > max_total:
            return plan, (
                f"User filters + tenant scopes produce {len(filters)} Semantic filters; "
                f"maximum is {max_total}."
            )

        scoped_spec = replace(
            plan.spec,
            filters=tuple(filters),
        )
        return replace(plan, spec=scoped_spec), None

    @staticmethod
    def _outside(
        requested: tuple[str, ...],
        allowed: frozenset[str],
    ) -> tuple[str, ...]:
        """返回不在 Allowlist 的对象；``*`` 表示该类型不额外限制。"""

        if not requested or "*" in allowed:
            return ()
        return tuple(
            sorted(
                set(requested)
                - set(allowed)
            )
        )

    def _object_scope_warning(
        self,
        plan: SemanticQueryPlan,
        context: RequestContext,
    ) -> str | None:
        """阻止 Planner 产生超出 RequestContext 的 Metric/Business Dimension。"""

        assert plan.spec is not None

        denied_metrics = self._outside(
            tuple(plan.spec.metric_names),
            context.allowed_metrics,
        )
        if denied_metrics:
            return (
                "Semantic query is outside allowed metric scope: "
                f"{list(denied_metrics)}"
            )

        business_group_by = tuple(
            item
            for item in plan.spec.group_by
            if not item.startswith("metric_time__")
        )
        filter_dimensions = tuple(
            item.dimension
            for item in plan.spec.filters
        )
        scope_dimensions = tuple(
            item.dimension
            for item in context.dimension_scopes
        )
        requested_dimensions = tuple(
            dict.fromkeys(
                (
                    *business_group_by,
                    *filter_dimensions,
                    *scope_dimensions,
                )
            )
        )

        denied_dimensions = self._outside(
            requested_dimensions,
            context.allowed_dimensions,
        )
        if denied_dimensions:
            return (
                "Semantic query is outside allowed dimension scope: "
                f"{list(denied_dimensions)}"
            )
        return None

    def _load_values(self) -> dict[str, set[str]]:
        """从 Policy 声明的 canonical CSV 加载可注入 Dimension Value。"""

        output: dict[str, set[str]] = {}
        for dimension, config in self.semantic_policy.get(
            "structured_filter_dimensions",
            {},
        ).items():
            source = config.get("value_source") or {}
            if source.get("type") != "csv":
                output[dimension] = set()
                continue

            path = self.root / str(source["path"])
            column = str(source["column"])
            values: set[str] = set()
            if path.exists():
                with path.open(
                    newline="",
                    encoding="utf-8",
                ) as handle:
                    for row in csv.DictReader(handle):
                        value = str(
                            row.get(column, "") or ""
                        ).strip()
                        if value:
                            values.add(value)
            output[dimension] = values
        return output
