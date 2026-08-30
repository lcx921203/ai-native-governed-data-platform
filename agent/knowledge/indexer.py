"""Governed Knowledge Indexer：从 Manifest Corpus 构建 Qdrant 索引。

真实重建索引需要显式 Runtime gate；静态 Fake 执行只证明代码契约，
不会生成 ``RUNTIME_VERIFIED`` evidence。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agent.knowledge.chunking import KnowledgeChunker
from agent.knowledge.corpus import KnowledgeCorpus
from agent.knowledge.qdrant_store import QdrantKnowledgeStore


class KnowledgeIndexRuntimeError(RuntimeError):
    """索引 Runtime gate、向量维度或 Qdrant 回查失败时的错误。"""

    pass


class KnowledgeIndexer:
    """把 Corpus → Chunk → Embedding → Qdrant Upsert 串成一次索引构建。"""

    def __init__(self, project_root: Path | str, *, embedding_provider: Any | None = None, store: Any | None = None, chunker: KnowledgeChunker | None = None):
        """加载 Phase 7B 索引 contract，并允许静态测试注入 provider/store/chunker。"""
        self.root = Path(project_root).resolve()
        self.contract = yaml.safe_load((self.root / "infra/contracts/phase7/knowledge_rag.yml").read_text(encoding="utf-8"))
        self.embedding_provider = embedding_provider
        self.store = store
        self.chunker = chunker or KnowledgeChunker()

    def build_chunks(self):
        """只从受治理 Corpus 读取 active 文档并做 Structure-aware Chunking。"""
        return self.chunker.chunk_documents(KnowledgeCorpus(self.root).load())

    def index(self, *, require_runtime_gate: bool = True) -> dict:
        """构建知识索引并返回可供 evidence writer 使用的观测摘要。

        Runtime 模式必须显式开启 ``PHASE7B_ALLOW_KNOWLEDGE_REINDEX``；写入后重新精确
        count Qdrant points，只有真实 gate 模式才把 payload 标为 RUNTIME_OBSERVED。
        静态 Fake 模式明确标记 ``INJECTED_STATIC_TEST``。
        """
        if require_runtime_gate and os.getenv("PHASE7B_ALLOW_KNOWLEDGE_REINDEX", "false").lower() != "true":
            raise KnowledgeIndexRuntimeError("REFUSED: set PHASE7B_ALLOW_KNOWLEDGE_REINDEX=true explicitly")
        chunks = self.build_chunks()
        provider = self.embedding_provider
        if provider is None:
            from agent.knowledge.embeddings import OpenAIKnowledgeEmbeddingProvider
            provider = OpenAIKnowledgeEmbeddingProvider()
        store = self.store or QdrantKnowledgeStore(collection=self.contract["rag"]["collection"])
        vectors = provider.embed([chunk.embedding_text for chunk in chunks])
        dimensions = len(vectors[0]) if vectors else getattr(provider, "dimensions", 1536)
        if any(len(vector) != dimensions for vector in vectors):
            raise KnowledgeIndexRuntimeError("Embedding dimensions are inconsistent")
        store.ensure_collection(dimensions=dimensions)
        store.upsert(chunks, vectors)
        observed = store.count()
        if observed < len(chunks):
            raise KnowledgeIndexRuntimeError(f"Qdrant point-count re-query failed: expected at least {len(chunks)}, observed {observed}")
        runtime_verified = bool(require_runtime_gate)
        payload = {
            "contract": "commerce_phase7b_knowledge_index_runtime",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runtime_verified": runtime_verified,
            "status": "KNOWLEDGE_INDEX_RUNTIME_VERIFIED" if runtime_verified else "KNOWLEDGE_INDEX_TEST_EXECUTION",
            "evidence_scope": "RUNTIME_OBSERVED" if runtime_verified else "INJECTED_STATIC_TEST",
            "collection": self.contract["rag"]["collection"],
            "document_count": len({chunk.document_id for chunk in chunks}),
            "chunk_count": len(chunks),
            "observed_point_count": observed,
            "embedding_dimensions": dimensions,
        }
        return payload


def write_runtime_evidence(root: Path, payload: dict) -> Path:
    """把真实索引运行结果写入 Phase 7B evidence 文件。

    调用方必须先保证 payload 来自 Runtime-gated index；本函数本身只负责持久化，
    不会把静态测试结果自动升级成 Runtime Verified。
    """
    output = root / ".runtime/evidence/phase7b/knowledge_index.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output
