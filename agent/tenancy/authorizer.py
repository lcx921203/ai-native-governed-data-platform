"""Single Agent Runtime 的确定性授权器。

能力 Scope 名称与现有 MCP Scope 语义保持一致：
- commerce:semantic:read
- commerce:metadata:read
- commerce:operations:read
- commerce:knowledge:read

这里不依赖 mcp_server 包，避免 Agent Runtime 反向依赖协议层。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .contracts import AuthorizationDecision, RequestContext


class GovernedRequestAuthorizer:
    """在 Context Loader / Tool Execution 前执行授权。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/tenant_runtime_policy.yml").read_text(
                encoding="utf-8"
            )
        )

    def resolve(
        self,
        request_context: RequestContext | None,
    ) -> RequestContext | None:
        """解析显式 Context；Production strict mode 不允许隐式 superuser。"""

        if request_context is not None:
            return request_context

        env_name = str(self.policy["strict_mode"]["require_context_env"])
        strict = os.getenv(env_name, "false").lower() == "true"
        if strict:
            return None

        if bool(self.policy["compatibility"]["allow_implicit_local_context"]):
            return RequestContext.local_compat()
        return None

    def authorize(
        self,
        route: Any,
        request_context: RequestContext | None,
    ) -> AuthorizationDecision:
        if request_context is None:
            return AuthorizationDecision(
                False,
                warnings=(
                    "Explicit RequestContext is required by the current runtime policy.",
                ),
            )

        intent = str(getattr(getattr(route, "intent", None), "value", getattr(route, "intent", "")))
        required = tuple(
            str(x)
            for x in self.policy.get("intent_required_scopes", {}).get(intent, ())
        )
        missing = sorted(set(required) - set(request_context.scopes))
        if missing:
            return AuthorizationDecision(
                False,
                required_scopes=required,
                warnings=(f"Missing capability scope(s): {missing}",),
            )

        kind = str(getattr(route, "target_kind", "") or "")
        target = str(getattr(route, "target_id", "") or "")

        if kind in {"metric", "metric_set"} and target:
            metrics = tuple(x for x in target.split(",") if x)
            warning = self._allow_objects(
                metrics,
                request_context.allowed_metrics,
                "metric",
            )
            if warning:
                return AuthorizationDecision(False, required, (warning,))

        if kind == "dataset" and target:
            warning = self._allow_objects(
                (target,),
                request_context.allowed_datasets,
                "dataset",
            )
            if warning:
                return AuthorizationDecision(False, required, (warning,))

        if kind == "entity" and target:
            warning = self._allow_objects(
                (target,),
                request_context.allowed_entities,
                "entity",
            )
            if warning:
                return AuthorizationDecision(False, required, (warning,))

        if kind == "dimension" and target:
            warning = self._allow_objects(
                (target,),
                request_context.allowed_dimensions,
                "dimension",
            )
            if warning:
                return AuthorizationDecision(False, required, (warning,))

            # Dimension Value Discovery 同时依赖显式 Metric Context。
            for step in getattr(route, "steps", ()) or ():
                arguments = getattr(step, "arguments", {}) or {}
                metrics = tuple(str(x) for x in arguments.get("metrics", ()) or ())
                if metrics:
                    warning = self._allow_objects(
                        metrics,
                        request_context.allowed_metrics,
                        "metric",
                    )
                    if warning:
                        return AuthorizationDecision(False, required, (warning,))

        if kind == "knowledge" and target:
            scopes = tuple(x for x in target.split(",") if x)
            warning = self._allow_objects(
                scopes,
                request_context.allowed_knowledge_scopes,
                "knowledge scope",
            )
            if warning:
                return AuthorizationDecision(False, required, (warning,))

        # Dimension Scope 自身也必须在授权维度 allowlist 内。
        scope_dimensions = tuple(item.dimension for item in request_context.dimension_scopes)
        warning = self._allow_objects(
            scope_dimensions,
            request_context.allowed_dimensions,
            "dimension scope",
        )
        if warning:
            return AuthorizationDecision(False, required, (warning,))

        return AuthorizationDecision(True, required_scopes=required)

    @staticmethod
    def _allow_objects(
        objects: tuple[str, ...],
        allowed: frozenset[str],
        label: str,
    ) -> str | None:
        if not objects:
            return None
        if "*" in allowed:
            return None
        denied = sorted(set(objects) - set(allowed))
        if denied:
            return f"Request is outside allowed {label} scope: {denied}"
        return None
