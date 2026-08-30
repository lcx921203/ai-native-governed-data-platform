"""Commerce MCP 的受治理 Tool Registry（工具注册与调度层）。

MCP 只是协议入口；真正允许执行哪些只读能力，仍由这个 Registry 根据
Deployment Profile、OAuth Scope 与既有 Governed Tool Surface 共同决定。
Registry 不重新实现 MetricFlow / DataHub / Dagster / Knowledge RAG 的业务逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.semantic_query.tool import query_semantic_metric, query_semantic_metrics
from agent.tools.governed_metadata import GovernedMetadataTools
from mcp_server.auth.profiles import PROFILES
from mcp_server.auth.scopes import MCP_BASE_READ, TOOL_REQUIRED_SCOPE
from mcp_server.models import MCPToolEnvelope


class MCPAuthorizationError(PermissionError):
    """MCP Tool 不满足注册、Profile 或 Scope 约束时抛出的授权错误。"""


@dataclass(frozen=True)
class MCPPrincipal:
    """一次 MCP 调用的最小身份上下文。

    ``subject`` 表示已验证主体；``scopes`` 是 JWT / 本地边界解析后的权限集合。
    Bearer Token 本身不会进入这个对象，也不会向下游透传。
    """

    subject: str
    scopes: frozenset[str]


class GovernedMCPRegistry:
    """把既有受治理只读能力适配成 MCP 可调用 Tool。

    业务逻辑：
    1. Profile 决定本部署能注册哪些 Tool；
    2. Scope 决定当前主体能否调用；
    3. dispatch 把调用转回已有 Governed Metadata / Semantic / Knowledge 能力；
    4. 输出统一成 MCPToolEnvelope，并保留原 Evidence 等级。

    工程边界：这里没有 SQL、Shell、DataHub Write、Dagster Run/Recovery 等写能力。
    """

    def __init__(self, project_root: Path | str, *, profile: str = "analyst", knowledge_tools=None):
        """创建一个指定部署 Profile 的只读 Registry。

        ``project_root`` 用于加载既有工程契约；``knowledge_tools`` 可在测试中注入 Fake，
        避免静态验收必须启动真实 Qdrant / Reranker Runtime。
        """
        self.root = Path(project_root).resolve()
        if profile not in PROFILES:
            raise ValueError(f"Unknown MCP deployment profile: {profile}")
        self.profile = profile
        self._visible = frozenset(PROFILES[profile])
        self.metadata = GovernedMetadataTools(self.root)
        self._knowledge_tools = knowledge_tools

    @property
    def visible_tools(self) -> frozenset[str]:
        """返回当前 Deployment Profile 允许注册的 Tool 名称集合。"""
        return self._visible

    def _knowledge(self):
        """按需构造 Knowledge Tool，避免不需要知识能力时提前初始化 Runtime 依赖。"""
        if self._knowledge_tools is None:
            from agent.knowledge.tools import GovernedKnowledgeTools
            self._knowledge_tools = GovernedKnowledgeTools(self.root)
        return self._knowledge_tools

    @staticmethod
    def required_scope(tool: str) -> str:
        """查询一个 Tool 对应的能力 Scope；未登记 Tool 必须 Fail Closed。"""
        try:
            return TOOL_REQUIRED_SCOPE[tool]
        except KeyError as exc:
            raise MCPAuthorizationError(f"Tool is not in the governed read-only MCP scope map: {tool}") from exc

    def authorize(self, tool: str, principal: MCPPrincipal) -> None:
        """执行 Profile + OAuth Scope 两层授权检查。

        调用必须同时具有 ``commerce:mcp:read`` 和具体能力 Scope；
        Prompt 或模型生成的参数不能绕过这一步。
        """
        if tool not in self._visible:
            raise MCPAuthorizationError(f"Tool {tool!r} is not registered by profile {self.profile!r}")
        required = {MCP_BASE_READ, self.required_scope(tool)}
        missing = required - set(principal.scopes)
        if missing:
            raise MCPAuthorizationError(f"Missing MCP scope(s): {sorted(missing)}")

    @staticmethod
    def _normalize(tool: str, result: Any) -> MCPToolEnvelope:
        """把不同 Governed Tool 的结果归一为 MCPToolEnvelope。

        这里仅做结构适配，不重新解释 Evidence；如果下游返回 STATIC_CONTRACT 或
        RETRIEVED_KNOWLEDGE，就原样保留，绝不在协议层升级成 RUNTIME_VERIFIED。
        """
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        if not isinstance(result, dict):
            result = {"status": "ANSWERED", "evidence": "STATIC_CONTRACT", "payload": {"result": result}}
        return MCPToolEnvelope(
            tool=tool,
            status=str(result.get("status", "ANSWERED")),
            evidence=str(result.get("evidence", "STATIC_CONTRACT")),
            payload=dict(result.get("payload") or result),
            warnings=[str(x) for x in result.get("warnings", [])],
            sources=list(result.get("sources", [])),
        )

    def dispatch(self, tool: str, arguments: dict[str, Any], principal: MCPPrincipal) -> MCPToolEnvelope:
        """授权后把 MCP Tool 调用转发给既有受治理执行面。

        Metadata / Runtime Context 回到 GovernedMetadataTools；Metric Query 回到 Semantic Tool；
        Knowledge Search / Fetch 回到 GovernedKnowledgeTools。未知 Tool 一律拒绝。
        """
        self.authorize(tool, principal)
        if tool in {
            "get_dataset_context", "get_lineage_context", "get_metric_context",
            "get_dimension_values", "resolve_dimension_value", "get_runtime_context",
        }:
            result = getattr(self.metadata, tool)(**arguments)
        elif tool == "query_semantic_metric":
            result = query_semantic_metric(self.root, **arguments)
        elif tool == "query_semantic_metrics":
            result = query_semantic_metrics(self.root, **arguments)
        elif tool in {"search_knowledge", "fetch_knowledge"}:
            result = getattr(self._knowledge(), tool)(**arguments)
        else:
            raise MCPAuthorizationError(f"Unknown governed MCP tool: {tool}")
        return self._normalize(tool, result)
