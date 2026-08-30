"""Structured Truth 与 Knowledge RAG 的 Claim Authority 仲裁。

RAG 只拥有解释性知识权威；指标数值、Owner、Runtime 状态等结构化 Claim
仍由 MetricFlow / DataHub / Dagster 负责，知识检索不能填补缺失真值。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AuthorityDecision:
    """一次 Claim 权威选择结果：是否接受、主证据源是谁、为什么。"""
    accepted: bool
    primary_source: str | None
    reason: str


class ClaimAuthorityMatrix:
    """Structured Truth 与 Retrieved Knowledge 之间的确定性优先级守门器。"""

    def __init__(self, project_root: Path | str):
        """读取全项目 ``claim_authority.yml``，不让 LLM 临场决定证据优先级。"""
        root = Path(project_root).resolve()
        self.matrix = yaml.safe_load((root / "agent/contracts/claim_authority.yml").read_text(encoding="utf-8"))["claim_authority"]

    def decide(self, claim_type: str, candidates: list[dict[str, Any]]) -> AuthorityDecision:
        """按 claim type 的 primary/fallback 顺序选择唯一可接受证据源。

        如果某 Claim 的 primary 结构化权威缺失，Knowledge RAG 只有在该 Claim 本身
        允许 RAG 时才能承担解释；否则直接拒绝，不能补造事实。
        """
        rule = self.matrix.get(claim_type)
        if not rule:
            return AuthorityDecision(False, None, f"Unknown claim type: {claim_type}")
        primary = list(rule.get("primary", []))
        for source in primary:
            if any(item.get("source") == source for item in candidates):
                return AuthorityDecision(True, source, "primary authority available")
        fallback = list(rule.get("fallback", []))
        for source in fallback:
            if any(item.get("source") == source for item in candidates):
                return AuthorityDecision(True, source, "primary unavailable; governed fallback used")
        if "knowledge_rag" in primary and any(item.get("source") == "knowledge_rag" for item in candidates):
            return AuthorityDecision(True, "knowledge_rag", "knowledge authority owns this explanatory claim")
        if any(item.get("source") == "knowledge_rag" for item in candidates):
            return AuthorityDecision(False, None, "RAG cannot replace missing structured authority")
        return AuthorityDecision(False, None, "Required authority unavailable")

    def validate_knowledge_claim(self, *, claim_type: str, value: Any) -> None:
        """对准备由 RAG 支撑的 Claim 做最后边界校验。

        特别禁止知识文档创建 ``metric_numeric_value`` 等 Runtime 数值 Claim。
        ``value`` 保留在接口中用于后续扩展，但当前验证只依赖 Claim 类型契约。
        """
        rule = self.matrix.get(claim_type) or {}
        if rule.get("rag_allowed") is False:
            raise ValueError(f"RAG is forbidden for claim type {claim_type}")
        if claim_type == "metric_numeric_value":
            raise ValueError("Retrieved knowledge cannot create numeric runtime claims")
