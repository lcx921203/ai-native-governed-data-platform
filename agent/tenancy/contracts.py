"""Multi-Tenant Request Context（多租户请求上下文）契约。

RequestContext 必须由可信入口（API Gateway / OAuth / MCP Auth Boundary）传入，
绝不能从用户 Prompt 中解析 tenant_id / scope / role。

V1 目标：
- 对 Intent / Metric / Dataset / Entity / Knowledge Scope 做 Fail-Closed 授权；
- 支持单值受治理 Dimension Scope，例如 tenant 只能访问 store__region=West；
- Dimension Scope 通过 ContextVar 传到 MetricFlow Executor，自动覆盖普通查询和 Analysis；
- 不把 Bearer Token / JWT 原文下传到 Agent。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DimensionScope:
    """强制注入 Semantic Query 的数据范围。

    V1 只支持一个维度一个 canonical value。
    多值范围需要未来引入受治理 IN operator，当前必须 Fail Closed。
    """

    dimension: str
    values: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "values": list(self.values),
        }


@dataclass(frozen=True)
class RequestContext:
    """一次 Agent Run 的可信身份与授权范围。"""

    tenant_id: str
    subject: str
    scopes: frozenset[str]
    roles: tuple[str, ...] = ()

    # "*" 表示该对象类型不做额外对象级限制；空集合表示没有任何对象权限。
    allowed_metrics: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))
    allowed_datasets: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))
    allowed_entities: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))
    allowed_dimensions: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))
    allowed_knowledge_scopes: frozenset[str] = field(default_factory=lambda: frozenset({"*"}))

    # 数据行级 / 域级强制范围。它不来自 Prompt。
    dimension_scopes: tuple[DimensionScope, ...] = ()

    # 兼容旧单租户测试环境。Production strict mode 禁止隐式 local context。
    implicit_local: bool = False

    @classmethod
    def local_compat(cls) -> "RequestContext":
        """旧工程兼容模式；生产环境必须通过 env gate 禁掉。"""

        return cls(
            tenant_id="local",
            subject="local",
            scopes=frozenset(
                {
                    "commerce:semantic:read",
                    "commerce:metadata:read",
                    "commerce:operations:read",
                    "commerce:knowledge:read",
                }
            ),
            implicit_local=True,
        )

    def to_dict(self) -> dict[str, Any]:
        """只输出安全身份摘要；不包含 Token / Secret。"""

        return {
            "tenant_id": self.tenant_id,
            "subject": self.subject,
            "scopes": sorted(self.scopes),
            "roles": list(self.roles),
            "allowed_metrics": sorted(self.allowed_metrics),
            "allowed_datasets": sorted(self.allowed_datasets),
            "allowed_entities": sorted(self.allowed_entities),
            "allowed_dimensions": sorted(self.allowed_dimensions),
            "allowed_knowledge_scopes": sorted(self.allowed_knowledge_scopes),
            "dimension_scopes": [item.to_dict() for item in self.dimension_scopes],
            "implicit_local": self.implicit_local,
        }


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    required_scopes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "required_scopes": list(self.required_scopes),
            "warnings": list(self.warnings),
        }
