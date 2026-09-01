"""Commerce MCP 的受治理 Tool Registry（工具注册、授权与 RequestContext 桥接）。

MCP 只是协议入口；真正允许执行哪些只读能力，仍由这个 Registry 根据
Deployment Profile、OAuth Scope、Trusted RequestContext 与既有 Governed Tool Surface
共同决定。

远程 MCP 与 HTTP Agent API 现在共享同一个 RequestContext：
- capability scope 决定“能不能调用这类能力”；
- object allowlist 决定“能访问哪些 Metric/Dataset/Dimension/Knowledge Scope”；
- Dimension Scope 通过 ContextVar 进入 MetricFlow Executor，形成真正的数据范围隔离。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.semantic_query.tool import (
    query_semantic_metric,
    query_semantic_metrics,
)
from agent.tenancy import (
    RequestContext,
    bind_request_context,
)
from agent.tools.governed_metadata import GovernedMetadataTools
from mcp_server.auth.profiles import PROFILES
from mcp_server.auth.scopes import (
    MCP_BASE_READ,
    TOOL_REQUIRED_SCOPE,
)
from mcp_server.models import MCPToolEnvelope


class MCPAuthorizationError(PermissionError):
    """MCP 调用不满足 Profile、Scope 或 RequestContext 时的授权错误。"""


@dataclass(frozen=True)
class MCPPrincipal:
    """一次 MCP 调用的可信最小身份上下文。

    ``scopes`` 负责协议能力授权；``request_context`` 负责统一 Tenant/Object/Row Scope。
    Bearer Token 本身不会进入本对象，也不会向下游透传。
    """

    subject: str
    scopes: frozenset[str]
    request_context: RequestContext | None = None


class GovernedMCPRegistry:
    """把既有受治理只读能力适配成 MCP Tool，并复用统一 RequestContext。"""

    def __init__(
        self,
        project_root: Path | str,
        *,
        profile: str = "analyst",
        knowledge_tools=None,
    ):
        self.root = Path(project_root).resolve()
        if profile not in PROFILES:
            raise ValueError(
                f"Unknown MCP deployment profile: {profile}"
            )
        self.profile = profile
        self._visible = frozenset(PROFILES[profile])
        self.metadata = GovernedMetadataTools(self.root)
        self._knowledge_tools = knowledge_tools

    @property
    def visible_tools(self) -> frozenset[str]:
        """返回当前 Deployment Profile 允许注册的 Tool 名称集合。"""

        return self._visible

    def _knowledge(self):
        """按需构造 Knowledge Tool，避免无知识请求时提前初始化 Runtime 依赖。"""

        if self._knowledge_tools is None:
            from agent.knowledge.tools import GovernedKnowledgeTools

            self._knowledge_tools = GovernedKnowledgeTools(
                self.root
            )
        return self._knowledge_tools

    @staticmethod
    def required_scope(tool: str) -> str:
        """查询 Tool 对应能力 Scope；未登记 Tool 必须 Fail Closed。"""

        try:
            return TOOL_REQUIRED_SCOPE[tool]
        except KeyError as exc:
            raise MCPAuthorizationError(
                "Tool is not in the governed read-only MCP "
                f"scope map: {tool}"
            ) from exc

    def authorize(
        self,
        tool: str,
        principal: MCPPrincipal,
    ) -> RequestContext:
        """执行 Profile + OAuth Scope + Trusted RequestContext 三层授权。"""

        if tool not in self._visible:
            raise MCPAuthorizationError(
                f"Tool {tool!r} is not registered by profile {self.profile!r}"
            )

        required = {
            MCP_BASE_READ,
            self.required_scope(tool),
        }
        missing = required - set(principal.scopes)
        if missing:
            raise MCPAuthorizationError(
                f"Missing MCP scope(s): {sorted(missing)}"
            )

        context = principal.request_context
        if context is None:
            raise MCPAuthorizationError(
                "Trusted RequestContext is required for governed MCP dispatch."
            )

        if (
            not context.tenant_id.strip()
            or not context.subject.strip()
        ):
            raise MCPAuthorizationError(
                "RequestContext tenant_id and subject are required."
            )

        if context.subject != principal.subject:
            raise MCPAuthorizationError(
                "MCPPrincipal subject does not match RequestContext subject."
            )

        if set(context.scopes) != set(principal.scopes):
            raise MCPAuthorizationError(
                "MCPPrincipal scopes do not match RequestContext scopes."
            )

        # Mandatory Dimension Scope 自身也必须处于该身份允许的 Dimension Allowlist 内。
        self._require_allowed(
            tuple(
                item.dimension
                for item in context.dimension_scopes
            ),
            context.allowed_dimensions,
            "dimension scope",
        )
        return context

    @staticmethod
    def _require_allowed(
        requested: tuple[str, ...],
        allowed: frozenset[str],
        label: str,
    ) -> None:
        """校验对象 Allowlist；``*`` 表示该对象类型不做额外限制。"""

        requested = tuple(
            item
            for item in (
                str(value).strip()
                for value in requested
            )
            if item
        )
        if not requested or "*" in allowed:
            return

        denied = sorted(
            set(requested)
            - set(allowed)
        )
        if denied:
            raise MCPAuthorizationError(
                f"MCP request is outside allowed {label} scope: {denied}"
            )

    def _govern_arguments(
        self,
        tool: str,
        arguments: dict[str, Any],
        context: RequestContext,
    ) -> dict[str, Any]:
        """在真正执行 Tool 前应用对象 Allowlist，并对 Knowledge Search 注入受控 Scope。"""

        governed = dict(arguments)

        if tool in {
            "get_dataset_context",
            "get_lineage_context",
            "get_runtime_context",
        }:
            self._require_allowed(
                (str(governed.get("dataset") or ""),),
                context.allowed_datasets,
                "dataset",
            )

        if tool in {
            "get_metric_context",
            "query_semantic_metric",
        }:
            self._require_allowed(
                (str(governed.get("metric") or ""),),
                context.allowed_metrics,
                "metric",
            )

        if tool in {
            "query_semantic_metrics",
            "get_dimension_values",
            "resolve_dimension_value",
        }:
            self._require_allowed(
                tuple(
                    str(item)
                    for item in (
                        governed.get("metrics")
                        or ()
                    )
                ),
                context.allowed_metrics,
                "metric",
            )

        if tool == "get_dimension_values":
            self._require_allowed(
                (str(governed.get("dimension") or ""),),
                context.allowed_dimensions,
                "dimension",
            )

        if tool == "resolve_dimension_value":
            dimension = str(
                governed.get("dimension") or ""
            ).strip()
            if dimension:
                self._require_allowed(
                    (dimension,),
                    context.allowed_dimensions,
                    "dimension",
                )
            elif "*" not in context.allowed_dimensions:
                # 受限身份不能让 Tool 在多个 Dimension 中自由猜测。
                raise MCPAuthorizationError(
                    "Restricted RequestContext requires an explicit dimension "
                    "for resolve_dimension_value."
                )

        if tool == "search_knowledge":
            requested = tuple(
                str(item)
                for item in (
                    governed.get("scopes")
                    or ()
                )
            )
            allowed = context.allowed_knowledge_scopes

            if requested:
                self._require_allowed(
                    requested,
                    allowed,
                    "knowledge",
                )
            elif "*" not in allowed:
                if not allowed:
                    raise MCPAuthorizationError(
                        "RequestContext has no allowed knowledge scopes."
                    )
                # 用户不传 scope 时，不允许底层 Retriever 默认搜索全部语料。
                governed["scopes"] = sorted(allowed)

        if tool == "fetch_knowledge":
            if (
                "*" not in context.allowed_knowledge_scopes
                and not context.allowed_knowledge_scopes
            ):
                raise MCPAuthorizationError(
                    "RequestContext has no allowed knowledge scopes."
                )

        return governed

    def _authorize_knowledge_result(
        self,
        tool: str,
        result: Any,
        context: RequestContext,
    ) -> None:
        """对 Knowledge 返回值做二次 Scope 校验，防止底层过滤回归导致越权内容外泄。"""

        if tool not in {
            "search_knowledge",
            "fetch_knowledge",
        }:
            return
        if not isinstance(result, dict):
            return

        status = str(result.get("status", ""))
        payload = dict(result.get("payload") or {})
        if status != "ANSWERED":
            return

        if tool == "search_knowledge":
            results = list(payload.get("results") or [])
            scopes: list[str] = []
            for item in results:
                scope = str(
                    (item or {}).get("scope") or ""
                ).strip()
                if not scope:
                    raise MCPAuthorizationError(
                        "Knowledge search result is missing governed scope metadata."
                    )
                scopes.append(scope)
            self._require_allowed(
                tuple(scopes),
                context.allowed_knowledge_scopes,
                "knowledge",
            )
            return

        scope = str(payload.get("scope") or "").strip()
        if not scope:
            raise MCPAuthorizationError(
                "Fetched knowledge chunk is missing governed scope metadata."
            )
        self._require_allowed(
            (scope,),
            context.allowed_knowledge_scopes,
            "knowledge",
        )

    @staticmethod
    def _normalize(
        tool: str,
        result: Any,
    ) -> MCPToolEnvelope:
        """把 Governed Tool 结果归一为 MCPToolEnvelope，不改变 Evidence 等级。"""

        if hasattr(result, "to_dict"):
            result = result.to_dict()
        if not isinstance(result, dict):
            result = {
                "status": "ANSWERED",
                "evidence": "STATIC_CONTRACT",
                "payload": {"result": result},
            }
        return MCPToolEnvelope(
            tool=tool,
            status=str(
                result.get("status", "ANSWERED")
            ),
            evidence=str(
                result.get(
                    "evidence",
                    "STATIC_CONTRACT",
                )
            ),
            payload=dict(
                result.get("payload")
                or result
            ),
            warnings=[
                str(x)
                for x in result.get("warnings", [])
            ],
            sources=list(
                result.get("sources", [])
            ),
        )

    def dispatch(
        self,
        tool: str,
        arguments: dict[str, Any],
        principal: MCPPrincipal,
    ) -> MCPToolEnvelope:
        """在共享 RequestContext 下授权并执行既有 Governed Tool。"""

        context = self.authorize(tool, principal)
        governed_arguments = self._govern_arguments(
            tool,
            arguments,
            context,
        )

        # 这是 MCP -> Agent Core Row Scope 的关键桥：
        # MetricFlowSemanticQueryExecutor 会从 ContextVar 读取同一个 RequestContext。
        with bind_request_context(context):
            if tool in {
                "get_dataset_context",
                "get_lineage_context",
                "get_metric_context",
                "get_dimension_values",
                "resolve_dimension_value",
                "get_runtime_context",
            }:
                result = getattr(
                    self.metadata,
                    tool,
                )(**governed_arguments)
            elif tool == "query_semantic_metric":
                result = query_semantic_metric(
                    self.root,
                    **governed_arguments,
                )
            elif tool == "query_semantic_metrics":
                result = query_semantic_metrics(
                    self.root,
                    **governed_arguments,
                )
            elif tool in {
                "search_knowledge",
                "fetch_knowledge",
            }:
                result = getattr(
                    self._knowledge(),
                    tool,
                )(**governed_arguments)
            else:
                raise MCPAuthorizationError(
                    f"Unknown governed MCP tool: {tool}"
                )

        self._authorize_knowledge_result(
            tool,
            result,
            context,
        )
        return self._normalize(tool, result)
