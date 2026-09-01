"""HTTP JWT -> RequestContext 的可信身份映射边界。

安全边界：
- Bearer Token 只交给现有 JWKS JWT Verifier 验证；
- 验证后只把明确允许的 claims 映射进 RequestContext；
- Token 原文不会进入 RequestContext、Agent Prompt、Trace 或 Tool 参数；
- 对象 allowlist 缺失时默认空集合（deny），不会默认 "*";
- tenant_id 必须来自已验证 JWT claim，不能来自用户问题。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent.tenancy import DimensionScope, RequestContext
from mcp_server.auth.jwt import VerifiedJWT


class AgentAPIIdentityError(RuntimeError):
    """JWT 已验证，但身份 claims 无法形成安全 RequestContext。"""


class AgentIdentityMapper:
    """把 VerifiedJWT 映射为 Agent Core 可消费的 RequestContext。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/agent_api_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        self.claims_policy = dict(self.policy["identity_claims"])
        self.limits = dict(self.policy["limits"])

    def map(self, verified: VerifiedJWT) -> RequestContext:
        """读取受治理 claims，并构造不含 Token 的 RequestContext。"""

        claims = dict(verified.claims)
        tenant_claim = str(self.claims_policy["tenant_id"])
        tenant_id = str(claims.get(tenant_claim) or "").strip()
        if not tenant_id:
            raise AgentAPIIdentityError(
                f"Verified JWT is missing required tenant claim: {tenant_claim}"
            )

        roles = self._string_values(
            claims.get(self.claims_policy["roles"]),
            label="roles",
            max_items=int(self.limits["max_roles"]),
        )

        allowed_metrics = self._object_allowlist(
            claims,
            "allowed_metrics",
        )
        allowed_datasets = self._object_allowlist(
            claims,
            "allowed_datasets",
        )
        allowed_entities = self._object_allowlist(
            claims,
            "allowed_entities",
        )
        allowed_dimensions = self._object_allowlist(
            claims,
            "allowed_dimensions",
        )
        allowed_knowledge_scopes = self._object_allowlist(
            claims,
            "allowed_knowledge_scopes",
        )

        dimension_scopes = self._dimension_scopes(
            claims.get(self.claims_policy["dimension_scopes"])
        )

        return RequestContext(
            tenant_id=tenant_id,
            subject=verified.subject,
            scopes=frozenset(verified.scopes),
            roles=roles,
            allowed_metrics=allowed_metrics,
            allowed_datasets=allowed_datasets,
            allowed_entities=allowed_entities,
            allowed_dimensions=allowed_dimensions,
            allowed_knowledge_scopes=allowed_knowledge_scopes,
            dimension_scopes=dimension_scopes,
            implicit_local=False,
        )

    def _object_allowlist(
        self,
        claims: dict[str, Any],
        policy_key: str,
    ) -> frozenset[str]:
        """读取一个对象 allowlist；claim 缺失时返回空集合，保持 Fail Closed。"""

        claim_name = str(self.claims_policy[policy_key])
        values = self._string_values(
            claims.get(claim_name),
            label=claim_name,
            max_items=int(self.limits["max_object_allowlist_items"]),
        )
        return frozenset(values)

    def _dimension_scopes(self, raw: Any) -> tuple[DimensionScope, ...]:
        """解析单值 Dimension Scope；V1 不接受多值或复杂表达式。"""

        if raw is None:
            return ()
        if not isinstance(raw, dict):
            raise AgentAPIIdentityError(
                "dimension_scopes claim must be an object mapping dimension -> value"
            )

        max_scopes = int(self.limits["max_dimension_scopes"])
        if len(raw) > max_scopes:
            raise AgentAPIIdentityError(
                f"dimension_scopes exceeds governed maximum {max_scopes}"
            )

        output: list[DimensionScope] = []
        for dimension, value in raw.items():
            name = str(dimension).strip()
            if not name:
                raise AgentAPIIdentityError(
                    "dimension_scopes contains an empty dimension name"
                )

            values = self._string_values(
                value,
                label=f"dimension_scopes.{name}",
                max_items=1,
            )
            if len(values) != 1:
                raise AgentAPIIdentityError(
                    f"dimension_scopes.{name} must contain exactly one value"
                )
            output.append(DimensionScope(name, values))
        return tuple(output)

    @staticmethod
    def _string_values(
        raw: Any,
        *,
        label: str,
        max_items: int,
    ) -> tuple[str, ...]:
        """把 string / array claim 规范化为去重字符串 tuple。"""

        if raw is None:
            return ()

        if isinstance(raw, str):
            candidates = (raw,)
        elif isinstance(raw, (list, tuple, set, frozenset)):
            candidates = tuple(raw)
        else:
            raise AgentAPIIdentityError(
                f"{label} must be a string or string array"
            )

        values: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, str):
                raise AgentAPIIdentityError(
                    f"{label} must contain strings only"
                )
            value = candidate.strip()
            if value and value not in values:
                values.append(value)

        if len(values) > max_items:
            raise AgentAPIIdentityError(
                f"{label} exceeds governed maximum {max_items}"
            )
        return tuple(values)
