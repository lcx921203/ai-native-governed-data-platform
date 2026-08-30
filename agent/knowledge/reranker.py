"""Knowledge RAG 第二阶段 Reranking（重排）适配层。

第一阶段 Qdrant Dense Retrieval 负责高召回候选；第二阶段 Cohere Reranker
按 query 重新排序。Reranker 失败时是否降级由 retrieval policy 决定。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

import cohere


@dataclass(frozen=True)
class RerankCandidate:
    """送入重排器的 Dense 候选，保留原始 rank/score 便于审计。"""
    chunk_id: str
    dense_rank: int
    dense_score: float
    payload: dict[str, Any]

    @property
    def rerank_text(self) -> str:
        """把标题、section、scope、authority 与正文组合成 Cohere 重排文本。"""
        return f"Title: {self.payload['title']}\nSection: {self.payload['section']}\nScope: {self.payload['scope']}\nAuthority: {self.payload['authority']}\n\n{self.payload['content']}"


@dataclass(frozen=True)
class RerankedCandidate:
    """第二阶段重排结果，同时保留 Dense 与 Rerank 两套排名/分数。"""
    chunk_id: str
    dense_rank: int
    dense_score: float
    rerank_rank: int
    rerank_score: float
    payload: dict[str, Any]


class RerankerProvider(Protocol):
    """可替换的重排 Provider 协议，便于 Runtime Provider 与静态 Fake 解耦。"""

    def rerank(self, *, query: str, candidates: list[RerankCandidate], top_n: int) -> list[RerankedCandidate]:
        """输入 query + 候选集合，返回最多 ``top_n`` 个有序重排结果。"""
        ...


class CohereKnowledgeReranker:
    """使用 Cohere Rerank API 的真实重排 Provider。"""

    def __init__(self, *, client=None, model: str | None = None):
        """从注入 Client 或 ``COHERE_API_KEY`` 构造 Provider，并固定模型来源。"""
        self.client = client or cohere.ClientV2(api_key=os.getenv('COHERE_API_KEY'))
        self.model = model or os.getenv('KNOWLEDGE_RERANK_MODEL', 'rerank-v4.0-pro')

    def rerank(self, *, query: str, candidates: list[RerankCandidate], top_n: int) -> list[RerankedCandidate]:
        """调用 Cohere Rerank API，并把 provider index 映射回原 Dense Candidate。

        Agent 无权指定任意 rerank model；模型由受治理环境变量/默认值决定。
        """
        if not candidates:
            return []
        response = self.client.rerank(model=self.model, query=query, documents=[c.rerank_text for c in candidates], top_n=min(top_n, len(candidates)))
        results = []
        for rank, item in enumerate(response.results, start=1):
            candidate = candidates[int(item.index)]
            results.append(RerankedCandidate(candidate.chunk_id, candidate.dense_rank, candidate.dense_score, rank, float(item.relevance_score), candidate.payload))
        return results
