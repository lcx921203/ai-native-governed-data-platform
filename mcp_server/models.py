"""MCP 对外结构化响应模型。

所有 MCP Tool 都归一化成统一 Envelope，保留 status / evidence / payload / warnings / sources，
避免协议层在转发时丢失前面章节建立的 Evidence Boundary（证据边界）。
"""

from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


class MCPToolEnvelope(BaseModel):
    """MCP Tool 的统一结构化输出。

    输入来自既有 Governed Tool 的结果；输出不会把 STATIC_CONTRACT、
    RETRIEVED_KNOWLEDGE 等证据等级擅自升级成 RUNTIME_VERIFIED。
    """

    tool: str
    status: str
    evidence: str
    payload: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
