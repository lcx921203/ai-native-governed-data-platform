"""MCP Resource（资源）读取适配。

Resource URI 不是任意文件路径：dataset / metric 必须是单个受治理 URI segment，
knowledge 必须是 exact governed chunk_id；最终读取仍然回到 GovernedMCPRegistry。
"""

from __future__ import annotations

from urllib.parse import unquote

from mcp_server.registry import GovernedMCPRegistry, MCPPrincipal


def _segment(value: str) -> str:
    """校验一个 MCP Resource 标识符只能是单个受治理 URI 段。

    拒绝 ``/``、``..`` 与反斜杠，避免 Resource API 退化成任意路径读取入口。
    """
    value = unquote(value).strip()
    if not value or "/" in value or ".." in value or "\\" in value:
        raise ValueError("resource identifier must be one governed URI segment")
    return value


def read_dataset_resource(registry: GovernedMCPRegistry, dataset: str, principal: MCPPrincipal) -> dict:
    """读取一个受治理 Dataset Resource。

    输入是 dataset 标识和调用主体；输出通过 Registry 的 ``get_dataset_context`` 生成，
    因此仍受 Profile、Scope 和 DataHub Metadata Authority 约束。
    """
    return registry.dispatch("get_dataset_context", {"dataset": _segment(dataset)}, principal).model_dump()


def read_metric_resource(registry: GovernedMCPRegistry, metric: str, principal: MCPPrincipal) -> dict:
    """读取一个受治理 Metric Resource。

    这里不会自行计算指标，只转交 ``get_metric_context``，Metric 定义/计算权威仍属于 MetricFlow。
    """
    return registry.dispatch("get_metric_context", {"metric": _segment(metric)}, principal).model_dump()


def read_knowledge_resource(registry: GovernedMCPRegistry, chunk_id: str, principal: MCPPrincipal) -> dict:
    """按 exact ``chunk_id`` 读取一个受治理知识块。

    必须包含 ``#c`` 的稳定 Chunk ID，拒绝路径式读取；Knowledge RAG 只能提供解释证据，
    不能因此升级成 Runtime Truth。
    """
    chunk_id = unquote(chunk_id).strip()
    if "/" in chunk_id or ".." in chunk_id or "#c" not in chunk_id:
        raise ValueError("exact governed chunk id required")
    return registry.dispatch("fetch_knowledge", {"chunk_id": chunk_id}, principal).model_dump()
