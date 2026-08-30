"""受治理 Knowledge Retrieval 主流程。

流程固定为：治理参数校验 → Runtime gate → Query Embedding → Qdrant Dense Retrieval
→ 可选 Cohere Rerank → Provenance-rich Hit；exact fetch 只能接受受治理 chunk_id。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agent.knowledge.qdrant_store import DenseHit, QdrantKnowledgeStore


@dataclass(frozen=True)
class KnowledgeSearchHit:
    """Agent 可消费的检索结果；同时保留 Dense/Rerank 分数与文档 SHA。"""
    chunk_id: str
    document_id: str
    title: str
    section: str
    scope: str
    authority: str
    source_path: str
    dense_rank: int
    dense_score: float
    rerank_rank: int | None
    rerank_score: float | None
    retrieval_mode: str
    content_preview: str
    document_sha256: str
    content_sha256: str
    source_format: str
    page_numbers: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        """转换为工具层可序列化字典，不改变证据字段。"""
        return self.__dict__.copy()


class GovernedKnowledgeRetriever:
    """把检索策略、Runtime gate、Qdrant 与 Reranker 串成受治理检索器。"""

    def __init__(self, project_root: Path | str, *, embedding_provider: Any | None = None, store: Any | None = None, reranker: Any | None = None):
        """加载 retrieval/knowledge policy，并允许静态测试注入 Fake Provider/Store/Reranker。"""
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load((self.root / "agent/contracts/knowledge_retrieval_policy.yml").read_text(encoding="utf-8"))
        self.knowledge_policy = yaml.safe_load((self.root / "agent/contracts/knowledge_policy.yml").read_text(encoding="utf-8"))
        self.embedding_provider = embedding_provider
        self.store = store
        self.reranker = reranker

    def _validate(self, *, scopes: list[str] | None, top_k: int) -> None:
        """校验 scope 白名单与 top_k 上限，阻止 Agent 构造任意检索空间。"""
        allowed = set(self.knowledge_policy["allowed_scopes"])
        if scopes and not set(scopes).issubset(allowed):
            raise ValueError(f"Unknown knowledge scope(s): {sorted(set(scopes) - allowed)}")
        if not 1 <= top_k <= int(self.policy["retrieval"]["reranking"]["max_final_top_k"]):
            raise ValueError("top_k outside governed range")

    def _runtime_ready(self) -> None:
        """读取 Phase 7B Runtime gate 与索引 evidence，未真实验证则 Fail Closed。

        只有 evidence 同时满足 ``runtime_verified=true`` 与要求的 status，
        才允许真实检索；源码/静态测试存在不能代替这个门禁。
        """
        if os.getenv(self.policy["runtime"]["allow_env"], "false").lower() != "true":
            raise RuntimeError(f"REFUSED: set {self.policy['runtime']['allow_env']}=true explicitly")
        path = self.root / self.policy["runtime"]["index_evidence"]
        if not path.exists():
            raise RuntimeError("Knowledge index runtime evidence is missing")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("runtime_verified") is not True or payload.get("status") != self.policy["runtime"]["required_index_status"]:
            raise RuntimeError("Knowledge index is not runtime verified")

    def search(self, query: str, *, scopes: list[str] | None = None, top_k: int = 5, domain: str | None = None, authorities: list[str] | None = None, require_runtime_gate: bool = True) -> list[KnowledgeSearchHit]:
        """执行两阶段受治理检索。

        Dense 阶段最多取 policy 指定候选；若 Rerank gate 打开则调用 Cohere 重新排序。
        Reranker 异常时按 policy 的 ``DENSE_FALLBACK`` 降级，并显式返回 retrieval_mode，
        而不是悄悄伪装成重排成功。
        """
        if not query.strip():
            raise ValueError("query must be non-empty")
        self._validate(scopes=scopes, top_k=top_k)
        if require_runtime_gate:
            self._runtime_ready()
        provider = self.embedding_provider
        if provider is None:
            from agent.knowledge.embeddings import OpenAIKnowledgeEmbeddingProvider
            provider = OpenAIKnowledgeEmbeddingProvider()
        store = self.store or QdrantKnowledgeStore()
        vector = provider.embed([query])[0]
        dense_cfg = self.policy["retrieval"]["dense"]
        dense = store.search(
            vector,
            limit=int(dense_cfg["candidate_top_k"]),
            minimum_score=float(dense_cfg["minimum_score"]),
            scopes=scopes,
            domain=domain,
            authorities=authorities,
        )
        if not dense:
            return []

        mode = "DENSE"
        reranked: list[Any] | None = None
        if os.getenv(self.policy["runtime"]["reranker_gate"], "false").lower() == "true" or self.reranker is not None:
            try:
                reranker = self.reranker
                if reranker is None:
                    from agent.knowledge.reranker import CohereKnowledgeReranker, RerankCandidate
                    reranker = CohereKnowledgeReranker()
                else:
                    from agent.knowledge.reranker import RerankCandidate
                candidates = [RerankCandidate(hit.chunk_id, rank, hit.score, hit.payload) for rank, hit in enumerate(dense, start=1)]
                reranked = reranker.rerank(query=query, candidates=candidates, top_n=top_k)
                mode = "RERANKED"
            except Exception:
                if self.policy["retrieval"]["reranking"]["failure_mode"] != "DENSE_FALLBACK":
                    raise
                reranked = None
                mode = "DENSE_FALLBACK"

        rows: list[KnowledgeSearchHit] = []
        if reranked is not None:
            for item in reranked[:top_k]:
                payload = item.payload
                rows.append(self._row(payload, item.chunk_id, item.dense_rank, item.dense_score, item.rerank_rank, item.rerank_score, mode))
        else:
            for rank, hit in enumerate(dense[:top_k], start=1):
                rows.append(self._row(hit.payload, hit.chunk_id, rank, hit.score, None, None, mode))
        return rows

    @staticmethod
    def _row(payload: dict, chunk_id: str, dense_rank: int, dense_score: float, rerank_rank: int | None, rerank_score: float | None, mode: str) -> KnowledgeSearchHit:
        """把底层 payload 规范化成受治理 Hit，并只生成有限长度 preview。"""
        content = str(payload.get("content", ""))
        preview = content if len(content) <= 320 else content[:317].rstrip() + "..."
        return KnowledgeSearchHit(
            chunk_id=chunk_id,
            document_id=str(payload.get("document_id", "")),
            title=str(payload.get("title", "")),
            section=str(payload.get("section", "")),
            scope=str(payload.get("scope", "")),
            authority=str(payload.get("authority", "")),
            source_path=str(payload.get("source_path", "")),
            dense_rank=dense_rank,
            dense_score=float(dense_score),
            rerank_rank=rerank_rank,
            rerank_score=None if rerank_score is None else float(rerank_score),
            retrieval_mode=mode,
            content_preview=preview,
            document_sha256=str(payload.get("document_sha256", "")),
            content_sha256=str(payload.get("content_sha256", "")),
            source_format=str(payload.get("source_format", "markdown")),
            page_numbers=tuple(int(x) for x in payload.get("page_numbers", []) if str(x).isdigit()),
        )

    def fetch(self, chunk_id: str, *, require_runtime_gate: bool = True) -> dict[str, Any] | None:
        """按 exact ``chunk_id`` 取回全文与 provenance。

        路径分隔符和 ``..`` 被拒绝，避免 Agent 把 fetch 退化成任意文件读取。
        返回 evidence 固定是 ``RETRIEVED_KNOWLEDGE``、``runtime_observed=False``。
        """
        if "#c" not in chunk_id or "/" in chunk_id or ".." in chunk_id:
            raise ValueError("exact governed chunk_id required")
        if require_runtime_gate:
            self._runtime_ready()
        store = self.store or QdrantKnowledgeStore()
        payload = store.fetch(chunk_id)
        if payload is None:
            return None
        return {
            "chunk_id": payload["chunk_id"],
            "document_id": payload["document_id"],
            "title": payload["title"],
            "section": payload["section"],
            "scope": payload["scope"],
            "domain": payload["domain"],
            "authority": payload["authority"],
            "source_path": payload["source_path"],
            "tags": list(payload.get("tags", [])),
            "content": payload["content"],
            "content_sha256": payload["content_sha256"],
            "document_sha256": payload["document_sha256"],
            "source_format": payload.get("source_format", "markdown"),
            "page_numbers": list(payload.get("page_numbers", [])),
            "evidence": "RETRIEVED_KNOWLEDGE",
            "runtime_observed": False,
        }
