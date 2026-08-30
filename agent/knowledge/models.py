"""Knowledge RAG 的稳定数据模型。

这里同时定义：
1. 统一文档结构 ``KnowledgeDocument / KnowledgeBlock``；
2. Structure-aware Chunking 后的 ``KnowledgeChunk``；
3. Qdrant point 的稳定 identity。

工程边界：这些对象只描述知识、来源与检索证据，不代表任何生产 Runtime 已被观察。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

KNOWLEDGE_POINT_NAMESPACE = uuid.UUID("57b7e9da-e0d4-462c-b758-f2b0ed9ba04f")


def stable_point_id(chunk_id: str) -> str:
    """把稳定 ``chunk_id`` 映射成稳定 Qdrant Point UUID。

    输入是受治理切片 ID，例如 ``document#c0001``；输出使用固定 UUID namespace
    做 UUIDv5。相同切片重复建索引会得到同一个 point_id，因此 Qdrant upsert
    可以幂等覆盖，而不是每次产生新的随机向量记录。
    """
    return str(uuid.uuid5(KNOWLEDGE_POINT_NAMESPACE, chunk_id))


@dataclass(frozen=True)
class KnowledgeBlock:
    """不同文档格式归一后的最小结构块。

    ``block_type`` 目前允许 heading / paragraph / table / code 等结构语义；
    ``section`` 表示标题路径，PDF 可以使用 ``Page N``；``page_number`` 仅在能可靠
    获取页码时填写。Chunker 只消费这个统一结构，不再关心源文件是 PDF、DOCX 还是 Markdown。
    """

    block_type: str
    text: str
    section: str = "Document"
    page_number: int | None = None


@dataclass(frozen=True)
class KnowledgeDocument:
    """一份通过 Manifest / Front Matter 校验后的受治理知识文档。

    ``source_format`` 记录 markdown / pdf / docx；``blocks`` 是多格式解析后统一结构。
    当前 Markdown 为保持既有稳定 chunk identity，正文仍由 Chunker 的 Markdown 结构解析器处理，
    因此 ``blocks`` 可以为空；PDF / DOCX 则直接携带解析后的结构块。

    ``document_sha256`` 固定源文件内容身份；authority / scope / owner 等治理字段
    来自 Front Matter 或 Manifest，用于过滤与 Claim provenance。
    """

    document_id: str
    title: str
    scope: str
    domain: str
    authority: str
    owner: str
    status: str
    tags: tuple[str, ...]
    source_path: Path
    reviewed_at: str | None
    body: str
    document_sha256: str
    source_format: str = "markdown"
    blocks: tuple[KnowledgeBlock, ...] = ()


@dataclass(frozen=True)
class KnowledgeChunk:
    """Structure-aware Chunking 后产生的最小检索单元。

    Grain 是一个稳定 ``chunk_id`` 一行；同时保留文档 SHA、内容 SHA、源格式和页码，
    让最终答案能够指出“从哪份文档的哪一块知识得到”，而不是只返回模糊文本。
    """

    point_id: str
    chunk_id: str
    document_id: str
    title: str
    section: str
    scope: str
    domain: str
    authority: str
    source_path: str
    tags: tuple[str, ...]
    chunk_index: int
    content: str
    content_sha256: str
    document_sha256: str
    source_format: str = "markdown"
    page_numbers: tuple[int, ...] = ()

    @property
    def embedding_text(self) -> str:
        """构造送入 Embedding API 的文本。

        标题 + section path + 正文一起向量化，使相同词语在不同章节里仍保留一定语境。
        这个字符串只是 embedding 输入，不改变原始 ``content`` 与其 SHA-256。
        """
        return f"{self.title}\n{self.section}\n\n{self.content}"
