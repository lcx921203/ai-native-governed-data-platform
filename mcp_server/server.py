"""Commerce MCP Server 的协议入口与 Transport（传输）边界。

本文件把既有 Governed Tool Registry 注册成 MCP Tool / Resource / Prompt，
并区分本地 stdio 与远程 Streamable HTTP 两种 Transport。

工程边界：
- stdio 依赖本地进程边界；
- Streamable HTTP 必须经过 OAuth Resource Server + JWT；
- MCP 不重新实现 MetricFlow / DataHub / Dagster / Knowledge RAG；
- MCP 不暴露 SQL、写入、Backfill、Recovery 等生产执行权限。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from mcp_server.auth.scopes import MCP_BASE_READ
from mcp_server.prompts import explain_metric, investigate_metric_issue
from mcp_server.registry import GovernedMCPRegistry, MCPPrincipal
from mcp_server.resources import read_dataset_resource, read_knowledge_resource, read_metric_resource

ROOT = Path(__file__).resolve().parents[1]


def _stdio_principal(registry: GovernedMCPRegistry) -> MCPPrincipal:
    """为本地 stdio Transport 构造进程边界内的只读 Principal。

    stdio 不经过远程 OAuth，但只授予当前 Deployment Profile 已注册 Tool 所需的
    基础只读 Scope + 具体能力 Scope；并不会因此获得 Registry 未注册的能力。
    """
    from mcp_server.auth.scopes import TOOL_REQUIRED_SCOPE
    scopes = {MCP_BASE_READ}
    scopes.update(TOOL_REQUIRED_SCOPE[t] for t in registry.visible_tools)
    return MCPPrincipal("local-stdio", frozenset(scopes))


def build_server(*, project_root: Path | str = ROOT, profile: str | None = None, http_auth: bool = False):
    """构造 Commerce MCPServer，并注册只读 Tool / Resource / Prompt。

    输入：
    - ``project_root``：当前 canonical source 根目录；
    - ``profile``：knowledge_only / analyst / operator_read；
    - ``http_auth``：是否启用 OAuth/JWT 的 HTTP Resource Server 边界。

    Framework/API：MCP Python SDK v2 的 ``MCPServer`` 负责协议注册与 Transport；
    本函数只做协议绑定，真正执行仍交给 ``GovernedMCPRegistry``。
    """
    try:
        from mcp.server import MCPServer
    except ImportError as exc:
        raise RuntimeError("MCP Python SDK v2 is not installed; install requirements-mcp.txt") from exc

    profile = profile or os.getenv("COMMERCE_MCP_PROFILE", "analyst")
    registry = GovernedMCPRegistry(project_root, profile=profile)
    token_verifier = None
    auth = None
    if http_auth:
        from pydantic import AnyHttpUrl
        from mcp.server.auth.provider import AccessToken
        from mcp.server.auth.settings import AuthSettings
        from mcp_server.auth.jwt import JWKSJWTVerifier, JWTVerificationError
        verifier = JWKSJWTVerifier()

        class TokenVerifierAdapter:
            """把项目的 JWKSJWTVerifier 适配成 MCP SDK 需要的 Token Verifier 接口。"""

            async def verify_token(self, token: str):
                """验证 Bearer Token；失败返回 None，成功只返回最小身份信息。

                注意 ``token='verified-not-forwarded'`` 是一个占位字符串，明确避免把真实
                Bearer Token 继续透传到 MCP Tool 或下游数据系统。
                """
                try:
                    verified = verifier.verify(token)
                except JWTVerificationError:
                    return None
                return AccessToken(
                    token="verified-not-forwarded",
                    client_id=verified.client_id,
                    scopes=list(verified.scopes),
                    expires_at=verified.expires_at,
                    subject=verified.subject,
                    claims=verified.claims,
                )
        token_verifier = TokenVerifierAdapter()
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(os.environ["MCP_AUTH_ISSUER"]),
            resource_server_url=AnyHttpUrl(os.environ["MCP_RESOURCE_URL"]),
            required_scopes=[MCP_BASE_READ],
        )

    mcp = MCPServer(
        "Commerce Governed MCP",
        instructions="Read-only governed commerce metadata, semantic, operational and knowledge access.",
        token_verifier=token_verifier,
        auth=auth,
    )

    def principal() -> MCPPrincipal:
        """把当前 Transport 的认证上下文转换成 Registry 使用的 MCPPrincipal。

        HTTP 模式从 MCP SDK Auth Context 读取已验证 AccessToken；没有身份时返回空 Scope，
        后续 Registry 会 Fail Closed。stdio 则使用本地只读 Principal。
        """
        if not http_auth:
            return _stdio_principal(registry)
        from mcp.server.auth.middleware.auth_context import get_access_token
        access = get_access_token()
        if access is None:
            return MCPPrincipal("unauthenticated", frozenset())
        return MCPPrincipal(str(access.subject or access.client_id or "authenticated"), frozenset(access.scopes or []))

    @mcp.tool()
    def get_dataset_context(dataset: str) -> dict:
        """读取受治理 Dataset Context；Dataset Identity / Owner / Governance 权威仍属于 DataHub。"""
        return registry.dispatch("get_dataset_context", {"dataset": dataset}, principal()).model_dump()

    @mcp.tool()
    def get_lineage_context(dataset: str, direction: str = "upstream", max_hops: int = 2) -> dict:
        """读取有界 DataHub Lineage；``max_hops`` 防止 MCP 暴露无界图遍历能力。"""
        return registry.dispatch("get_lineage_context", {"dataset": dataset, "direction": direction, "max_hops": max_hops}, principal()).model_dump()

    @mcp.tool()
    def get_metric_context(metric: str) -> dict:
        """读取受治理 Metric 定义；MCP 只转发，Metric Definition Authority 仍属于 MetricFlow。"""
        return registry.dispatch("get_metric_context", {"metric": metric}, principal()).model_dump()

    @mcp.tool()
    def query_semantic_metric(metric: str, question: str, limit: int = 20) -> dict:
        """查询一个受治理 MetricFlow 指标；协议面没有 arbitrary SQL / raw WHERE 入口。"""
        return registry.dispatch("query_semantic_metric", {"metric": metric, "question": question, "limit": limit}, principal()).model_dump()

    @mcp.tool()
    def query_semantic_metrics(metrics: list[str], question: str, limit: int = 20) -> dict:
        """查询两到三个已批准 Metric，用于受治理比较；不允许模型自由拼 SQL。"""
        return registry.dispatch("query_semantic_metrics", {"metrics": metrics, "question": question, "limit": limit}, principal()).model_dump()

    @mcp.tool()
    def get_dimension_values(metrics: list[str], dimension: str, question: str = "", limit: int = 25) -> dict:
        """发现受治理 Semantic Dimension 的候选值，用于 Clarification / Value Resolution。"""
        return registry.dispatch("get_dimension_values", {"metrics": metrics, "dimension": dimension, "question": question, "limit": limit}, principal()).model_dump()

    @mcp.tool()
    def resolve_dimension_value(metrics: list[str], raw_value: str, dimension: str | None = None, question: str = "") -> dict:
        """把用户原始业务值解析成受治理 Dimension Value；不能绕过 Semantic Contract。"""
        return registry.dispatch("resolve_dimension_value", {"metrics": metrics, "raw_value": raw_value, "dimension": dimension, "question": question}, principal()).model_dump()

    @mcp.tool()
    def get_runtime_context(dataset: str) -> dict:
        """读取 Dagster Operational Runtime Context；只读查询，不暴露 Run / Retry / Recovery handle。"""
        return registry.dispatch("get_runtime_context", {"dataset": dataset}, principal()).model_dump()

    @mcp.tool()
    def search_knowledge(query: str, scopes: list[str] | None = None, top_k: int = 5, domain: str | None = None, authorities: list[str] | None = None) -> dict:
        """搜索受治理 Knowledge RAG；结果证据等级仍是 RETRIEVED_KNOWLEDGE，而非 Runtime Truth。"""
        return registry.dispatch("search_knowledge", {"query": query, "scopes": scopes, "top_k": top_k, "domain": domain, "authorities": authorities}, principal()).model_dump()

    @mcp.tool()
    def fetch_knowledge(chunk_id: str) -> dict:
        """按 exact governed ``chunk_id`` 读取单个知识块，禁止退化成任意文件读取。"""
        return registry.dispatch("fetch_knowledge", {"chunk_id": chunk_id}, principal()).model_dump()

    @mcp.resource("commerce://dataset/{dataset}")
    def dataset_resource(dataset: str) -> dict:
        """把受治理 Dataset Context 暴露为 ``commerce://dataset/...`` Resource。"""
        return read_dataset_resource(registry, dataset, principal())

    @mcp.resource("commerce://metric/{metric}")
    def metric_resource(metric: str) -> dict:
        """把受治理 Metric Context 暴露为 ``commerce://metric/...`` Resource。"""
        return read_metric_resource(registry, metric, principal())

    @mcp.resource("commerce://knowledge/{chunk_id}")
    def knowledge_resource(chunk_id: str) -> dict:
        """把 exact Governed Knowledge Chunk 暴露为 MCP Resource。"""
        return read_knowledge_resource(registry, chunk_id, principal())

    @mcp.prompt()
    def explain_metric_prompt(metric: str) -> str:
        """返回“解释指标”的受治理 Prompt Template；Prompt 本身不执行 Tool。"""
        return explain_metric(metric)

    @mcp.prompt()
    def investigate_metric_issue_prompt(metric: str, partition_date: str) -> str:
        """返回“调查指标异常”的受治理 Prompt Template，不授予 Recovery / Backfill 权限。"""
        return investigate_metric_issue(metric, partition_date)

    return mcp


def main() -> int:
    """CLI 入口：根据 ``--transport`` 启动 stdio 或 Streamable HTTP MCP Server。

    HTTP 模式额外开启 DNS Rebinding Protection、Allowed Hosts 与 Allowed Origins；
    ``stateless_http=True`` 表示服务端不依赖隐式会话状态承载授权。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default=os.getenv("MCP_TRANSPORT", "stdio"))
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8000")))
    args = parser.parse_args()
    http = args.transport == "streamable-http"
    mcp = build_server(http_auth=http)
    if not http:
        mcp.run(transport="stdio")
        return 0
    from mcp.server.transport_security import TransportSecuritySettings
    hosts = [x.strip() for x in os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*",).split(",") if x.strip()]
    origins = [x.strip() for x in os.getenv("MCP_ALLOWED_ORIGINS", "http://localhost:*",).split(",") if x.strip()]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)
    mcp.run(transport="streamable-http", host=args.host, port=args.port, stateless_http=True, transport_security=security)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
