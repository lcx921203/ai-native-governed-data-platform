"""暴露给 Agent 的受治理 Knowledge Tools。

工具面只允许 ``search_knowledge`` 与 exact ``fetch_knowledge``；返回来源路径与 SHA，
同时固定 ``runtime_verified=False``，防止检索文档冒充生产运行事实。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.knowledge.retrieval import GovernedKnowledgeRetriever


class GovernedKnowledgeTools:
    """把 GovernedKnowledgeRetriever 封装成稳定 Agent Tool Contract。"""

    def __init__(self, project_root: Path | str, *, retriever: GovernedKnowledgeRetriever | None = None):
        """绑定工程根目录，并允许测试注入已配置的 Retriever。"""
        self.root = Path(project_root).resolve()
        self.retriever = retriever or GovernedKnowledgeRetriever(self.root)

    def search_knowledge(self, *, query: str, scopes: list[str] | None = None, top_k: int = 5, domain: str | None = None, authorities: list[str] | None = None) -> dict[str, Any]:
        """执行受治理搜索并返回有限 preview + provenance source 列表。

        evidence 固定为 ``RETRIEVED_KNOWLEDGE``；每个 source 携带 document/content SHA，
        但 ``runtime_verified`` 永远是 False。
        """
        try:
            hits = self.retriever.search(query, scopes=scopes, top_k=top_k, domain=domain, authorities=authorities)
        except RuntimeError as exc:
            # Runtime gate 未满足时返回 DEFERRED，而不是让 Agent 把“没有真实索引”误判成 NOT_FOUND。
            return {
                "tool": "search_knowledge",
                "status": "DEFERRED",
                "evidence": "DEFERRED",
                "payload": {"results": [], "count": 0},
                "warnings": [str(exc)],
                "sources": [],
            }
        return {
            "tool": "search_knowledge",
            "status": "ANSWERED" if hits else "NOT_FOUND",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": {"results": [hit.to_dict() for hit in hits], "count": len(hits)},
            "warnings": [],
            "sources": [
                {
                    "kind": "knowledge_chunk",
                    "location": hit.chunk_id,
                    "source_path": hit.source_path,
                    "document_sha256": hit.document_sha256,
                    "content_sha256": hit.content_sha256,
                    "source_format": getattr(hit, "source_format", "markdown"),
                    "page_numbers": list(getattr(hit, "page_numbers", ())),
                    "runtime_verified": False,
                }
                for hit in hits
            ],
        }

    def fetch_knowledge(self, *, chunk_id: str) -> dict[str, Any]:
        """按 Search 返回的 exact chunk_id 取回完整知识切片。

        Search 与 Fetch 分离可以让 Agent 先看候选，再明确取某个受治理 chunk；
        它不能把任意文件路径传进来读取。
        """
        try:
            payload = self.retriever.fetch(chunk_id)
        except RuntimeError as exc:
            return {"tool": "fetch_knowledge", "status": "DEFERRED", "evidence": "DEFERRED", "payload": {}, "warnings": [str(exc)], "sources": []}
        if payload is None:
            return {"tool": "fetch_knowledge", "status": "NOT_FOUND", "evidence": "RETRIEVED_KNOWLEDGE", "payload": {}, "warnings": [], "sources": []}
        return {
            "tool": "fetch_knowledge",
            "status": "ANSWERED",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": payload,
            "warnings": [],
            "sources": [{"kind": "knowledge_chunk", "location": chunk_id, "source_path": payload["source_path"], "source_format": payload.get("source_format", "markdown"), "page_numbers": list(payload.get("page_numbers", [])), "runtime_verified": False}],
        }
