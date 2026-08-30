"""Qdrant 向量存储的受治理适配层。

这里只暴露固定 collection 上的 ensure / upsert / filtered search / exact fetch；
Agent 不能传任意 collection、任意文件路径或任意 Qdrant filter。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from agent.knowledge.models import KnowledgeChunk, stable_point_id


@dataclass(frozen=True)
class DenseHit:
    """一次 Qdrant Dense Retrieval 返回的候选切片及相似度分数。"""
    chunk_id: str
    score: float
    payload: dict[str, Any]


class QdrantKnowledgeStore:
    """围绕 ``qdrant-client`` 的最小受治理适配器。

    ``qdrant-client`` 采用 lazy import，保证没有安装 Phase 7B Runtime 依赖时仓库仍可做
    source/static validation。真实 Client 只有在 Runtime gate 打开后才应该使用。
    """

    def __init__(self, *, client: Any | None = None, url: str = "http://localhost:6333", collection: str = "commerce_knowledge_v1"):
        """绑定固定 Qdrant Client 与 collection；测试可注入 Fake Client。"""
        if client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:
                raise RuntimeError("qdrant-client is not installed; install requirements-rag.txt") from exc
            client = QdrantClient(url=url)
        self.client = client
        self.collection = collection

    @staticmethod
    def payload(chunk: KnowledgeChunk) -> dict[str, Any]:
        """把 ``KnowledgeChunk`` 投影成 Qdrant payload，并保留 provenance / SHA 字段。"""
        return {
            "point_id": chunk.point_id,
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "title": chunk.title,
            "section": chunk.section,
            "scope": chunk.scope,
            "domain": chunk.domain,
            "authority": chunk.authority,
            "source_path": chunk.source_path,
            "tags": list(chunk.tags),
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "content_sha256": chunk.content_sha256,
            "document_sha256": chunk.document_sha256,
            "source_format": chunk.source_format,
            "page_numbers": list(chunk.page_numbers),
        }

    def ensure_collection(self, *, dimensions: int) -> None:
        """确保 collection 存在且向量维度与当前 Embedding Provider 一致。

        不存在则以 COSINE distance 创建；已存在但维度不一致则 Fail Closed，
        避免把新旧 embedding 模型产生的不同向量空间混在一起。
        """
        from qdrant_client import models

        names = {item.name for item in self.client.get_collections().collections}
        if self.collection not in names:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE),
            )
            return
        info = self.client.get_collection(self.collection)
        vectors = getattr(getattr(info, "config", None), "params", None)
        vector_params = getattr(vectors, "vectors", None)
        size = getattr(vector_params, "size", dimensions)
        if size != dimensions:
            raise RuntimeError(f"Qdrant collection dimension mismatch: expected={dimensions}, observed={size}")

    def upsert(self, chunks: list[KnowledgeChunk], vectors: list[list[float]]) -> None:
        """按稳定 point_id 把 chunk + vector 幂等写入 Qdrant。

        先校验 chunk/vector 基数一一对应；``wait=True`` 要求 Qdrant 完成写入后再返回。
        """
        if len(chunks) != len(vectors):
            raise ValueError("chunk/vector cardinality mismatch")
        from qdrant_client import models

        points = [
            models.PointStruct(id=chunk.point_id, vector=vector, payload=self.payload(chunk))
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        if points:
            self.client.upsert(collection_name=self.collection, points=points, wait=True)

    def count(self) -> int:
        """精确回查当前 collection point 数，用于索引后 Runtime Evidence 校验。"""
        result = self.client.count(collection_name=self.collection, exact=True)
        return int(result.count)

    @staticmethod
    def _filter(*, scopes: list[str] | None, domain: str | None, authorities: list[str] | None):
        """把经过治理层验证的 scope/domain/authority 转成 Qdrant payload filter。"""
        from qdrant_client import models

        must = []
        if scopes:
            must.append(models.FieldCondition(key="scope", match=models.MatchAny(any=list(scopes))))
        if domain:
            must.append(models.FieldCondition(key="domain", match=models.MatchValue(value=domain)))
        if authorities:
            must.append(models.FieldCondition(key="authority", match=models.MatchAny(any=list(authorities))))
        return models.Filter(must=must) if must else None

    def search(
        self,
        vector: list[float],
        *,
        limit: int,
        minimum_score: float,
        scopes: list[str] | None = None,
        domain: str | None = None,
        authorities: list[str] | None = None,
) -> list[DenseHit]:
        """执行 Dense Retrieval，并在向量检索前应用 payload filter。

        ``score_threshold`` 过滤低相似度结果；返回值只携带受治理 payload，
        后续是否 rerank 由 Retriever 决定。
        """
        response = self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=self._filter(scopes=scopes, domain=domain, authorities=authorities),
            limit=limit,
            score_threshold=minimum_score,
            with_payload=True,
        )
        points = getattr(response, "points", response)
        hits = []
        for point in points:
            payload = dict(getattr(point, "payload", None) or {})
            hits.append(DenseHit(str(payload.get("chunk_id") or point.id), float(point.score), payload))
        return hits

    def fetch(self, chunk_id: str) -> dict[str, Any] | None:
        """通过稳定 chunk_id 精确取回一个知识切片，而不是做模糊搜索。"""
        point_id = stable_point_id(chunk_id)
        points = self.client.retrieve(collection_name=self.collection, ids=[point_id], with_payload=True, with_vectors=False)
        if not points:
            return None
        payload = dict(points[0].payload or {})
        return payload if payload.get("chunk_id") == chunk_id else None
