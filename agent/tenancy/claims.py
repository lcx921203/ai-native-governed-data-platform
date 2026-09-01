"""Verified Identity Claims -> RequestContext 的共享可信映射。

HTTP Agent API 与远程 MCP 都只能把“已经通过协议入口验签”的身份结果交给本模块。
这里不解析 Bearer Token，也不读取 Prompt；只把允许的结构化 claims 投影成
Agent Core 统一使用的 ``RequestContext``。

这样 tenant / object allowlist / dimension scope 不再由两个协议入口各维护一套逻辑。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from .contracts import DimensionScope, RequestContext


class TrustedIdentityError(RuntimeError):
    """已验证身份无法形成安全 RequestContext 时的 Fail-Closed 错误。"""


class TrustedClaimsContextMapper:
    """把已验证 subject/scopes/claims 映射成统一 RequestContext。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (
                self.root
                / "agent/contracts/trusted_identity_policy.yml"
            ).read_text(encoding="utf-8")
        )
        self.claims_policy = dict(self.policy["identity_claims"])
        self.limits = dict(self.policy["limits"])

    def map(
        self,
        *,
        subject: str,
        scopes: Iterable[str],
        claims: dict[str, Any],
    ) -> RequestContext:
        """从可信身份结果构造不含 Token/JWT 原文的 RequestContext。"""

        normalized_subject = str(subject or "").strip()
        if not normalized_subject:
            raise TrustedIdentityError(
                "Verified identity subject is required."
            )

        safe_claims = dict(claims or {})
        claim_subject = str(safe_claims.get("sub") or "").strip()
        if claim_subject and claim_subject != normalized_subject:
            raise TrustedIdentityError(
                "Verified subject does not match the sub claim."
            )

        tenant_claim = str(self.claims_policy["tenant_id"])
        tenant_id = str(
            safe_claims.get(tenant_claim) or ""
        ).strip()
        if not tenant_id:
            raise TrustedIdentityError(
                f"Verified identity is missing required tenant claim: {tenant_claim}"
            )

        normalized_scopes = frozenset(
            value
            for value in (
                str(item).strip()
                for item in scopes
            )
            if value
        )

        roles = self._string_values(
            safe_claims.get(self.claims_policy["roles"]),
            label="roles",
            max_items=int(self.limits["max_roles"]),
        )

        dimension_scopes = self._dimension_scopes(
            safe_claims.get(
                self.claims_policy["dimension_scopes"]
            )
        )

        return RequestContext(
            tenant_id=tenant_id,
            subject=normalized_subject,
            scopes=normalized_scopes,
            roles=roles,
            allowed_metrics=self._object_allowlist(
                safe_claims,
                "allowed_metrics",
            ),
            allowed_datasets=self._object_allowlist(
                safe_claims,
                "allowed_datasets",
            ),
            allowed_entities=self._object_allowlist(
                safe_claims,
                "allowed_entities",
            ),
            allowed_dimensions=self._object_allowlist(
                safe_claims,
                "allowed_dimensions",
            ),
            allowed_knowledge_scopes=self._object_allowlist(
                safe_claims,
                "allowed_knowledge_scopes",
            ),
            dimension_scopes=dimension_scopes,
            implicit_local=False,
        )

    def _object_allowlist(
        self,
        claims: dict[str, Any],
        policy_key: str,
    ) -> frozenset[str]:
        """读取对象 Allowlist；缺失 claim 返回空集合而不是隐式 ``*``。"""

        claim_name = str(self.claims_policy[policy_key])
        return frozenset(
            self._string_values(
                claims.get(claim_name),
                label=claim_name,
                max_items=int(
                    self.limits["max_object_allowlist_items"]
                ),
            )
        )

    def _dimension_scopes(
        self,
        raw: Any,
    ) -> tuple[DimensionScope, ...]:
        """解析受信任的单值 Dimension Scope；多值语义当前 Fail Closed。"""

        if raw is None:
            return ()
        if not isinstance(raw, dict):
            raise TrustedIdentityError(
                "dimension_scopes claim must be an object mapping dimension -> value"
            )

        maximum = int(self.limits["max_dimension_scopes"])
        if len(raw) > maximum:
            raise TrustedIdentityError(
                f"dimension_scopes exceeds governed maximum {maximum}"
            )

        output: list[DimensionScope] = []
        for dimension, value in raw.items():
            name = str(dimension).strip()
            if not name:
                raise TrustedIdentityError(
                    "dimension_scopes contains an empty dimension name"
                )

            values = self._string_values(
                value,
                label=f"dimension_scopes.{name}",
                max_items=1,
            )
            if len(values) != 1:
                raise TrustedIdentityError(
                    f"dimension_scopes.{name} must contain exactly one value"
                )
            output.append(
                DimensionScope(
                    dimension=name,
                    values=values,
                )
            )
        return tuple(output)

    @staticmethod
    def _string_values(
        raw: Any,
        *,
        label: str,
        max_items: int,
    ) -> tuple[str, ...]:
        """把 string/string-array claim 规范化成有界去重 tuple。"""

        if raw is None:
            return ()

        if isinstance(raw, str):
            candidates = (raw,)
        elif isinstance(
            raw,
            (list, tuple, set, frozenset),
        ):
            candidates = tuple(raw)
        else:
            raise TrustedIdentityError(
                f"{label} must be a string or string array"
            )

        values: list[str] = []
        for candidate in candidates:
            if not isinstance(candidate, str):
                raise TrustedIdentityError(
                    f"{label} must contain strings only"
                )
            value = candidate.strip()
            if value and value not in values:
                values.append(value)

        if len(values) > max_items:
            raise TrustedIdentityError(
                f"{label} exceeds governed maximum {max_items}"
            )
        return tuple(values)
