"""Structure-aware Chunking（结构感知切片）。

Markdown 优先尊重标题、段落和 fenced code block；PDF / DOCX 则消费统一 ``KnowledgeBlock``。
Chunker 不关心原文件解析 API，只关心已经治理好的文档结构、Grain 与 provenance。
"""

from __future__ import annotations

import hashlib
import re

from agent.knowledge.models import KnowledgeBlock, KnowledgeChunk, KnowledgeDocument, stable_point_id


class KnowledgeChunkingError(RuntimeError):
    """文档无法在不破坏语义边界的前提下切片时抛出的错误。"""


def _sha256(value: str) -> str:
    """计算切片内容 SHA-256，供 provenance 与 exact fetch 校验使用。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _semantic_blocks(body: str) -> list[KnowledgeBlock]:
    """把 Markdown 正文解析成统一 ``KnowledgeBlock``。

    标题更新 section path；普通段落与 fenced code block 都是不可拆 atomic block。
    超长 atomic block 不在这里强行切碎，而由后续校验拒绝，要求源知识重新组织。
    """
    section_stack: list[str] = []
    current_section = "Document"
    blocks: list[KnowledgeBlock] = []
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        """把当前累计段落落成一个语义块，并清空段落缓冲区。"""
        nonlocal paragraph
        text = "\n".join(paragraph).strip()
        if text:
            blocks.append(KnowledgeBlock("paragraph", text, current_section))
        paragraph = []

    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            if not in_code:
                flush_paragraph()
                in_code = True
                code = [line]
            else:
                code.append(line)
                blocks.append(KnowledgeBlock("code", "\n".join(code).strip(), current_section))
                code = []
                in_code = False
            continue
        if in_code:
            code.append(line)
            continue

        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            flush_paragraph()
            level = len(match.group(1))
            title = match.group(2).strip()
            section_stack[:] = section_stack[: level - 1]
            while len(section_stack) < level - 1:
                section_stack.append("")
            section_stack.append(title)
            current_section = " > ".join(part for part in section_stack if part)
            continue
        if not line.strip():
            flush_paragraph()
            continue
        paragraph.append(line)

    if in_code:
        raise KnowledgeChunkingError("Unclosed fenced code block in knowledge document")
    flush_paragraph()
    return blocks


class KnowledgeChunker:
    """把受治理的多格式文档稳定切成可索引 ``KnowledgeChunk``。"""

    def __init__(self, *, max_chars: int = 2400, target_chars: int = 1600):
        """配置目标切片大小与单个语义块允许的最大字符数。

        ``target_chars`` 是合并语义块时的软目标；``max_chars`` 是单个 atomic block 的硬上限。
        """
        if not 400 <= target_chars <= max_chars:
            raise ValueError("target_chars must be between 400 and max_chars")
        self.max_chars = max_chars
        self.target_chars = target_chars

    @staticmethod
    def _blocks(document: KnowledgeDocument) -> list[KnowledgeBlock]:
        """选择统一结构块来源。

        PDF / DOCX 已由 Parser 产生 ``document.blocks``；Markdown 为保持当前切片稳定性，
        继续从 ``body`` 做标题/段落/code 解析。
        """
        return list(document.blocks) if document.blocks else _semantic_blocks(document.body)

    def chunk_document(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        """把单份文档切成稳定 chunk，并保留 section / page / source_format / SHA provenance。

        相同源文档在内容未变化时会得到相同 chunk_id 与 point_id；如果一个不可拆语义块
        超过 ``max_chars``，直接失败而不是静默破坏语义。
        """
        blocks = self._blocks(document)
        if not blocks:
            return []
        for block in blocks:
            if len(block.text) > self.max_chars:
                raise KnowledgeChunkingError(
                    f"{document.document_id}: semantic block in {block.section!r} exceeds max_chars={self.max_chars}; refactor source instead of silent splitting"
                )

        groups: list[tuple[str, list[KnowledgeBlock]]] = []
        current_section = blocks[0].section
        current: list[KnowledgeBlock] = []
        size = 0
        for block in blocks:
            projected = size + (2 if current else 0) + len(block.text)
            if current and (block.section != current_section or projected > self.target_chars):
                groups.append((current_section, current))
                current, size = [], 0
                current_section = block.section
            current.append(block)
            size += (2 if size else 0) + len(block.text)
        if current:
            groups.append((current_section, current))

        chunks: list[KnowledgeChunk] = []
        for index, (section, parts) in enumerate(groups, start=1):
            content = "\n\n".join(part.text for part in parts).strip()
            chunk_id = f"{document.document_id}#c{index:04d}"
            page_numbers = tuple(sorted({part.page_number for part in parts if part.page_number is not None}))
            chunks.append(
                KnowledgeChunk(
                    point_id=stable_point_id(chunk_id),
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    title=document.title,
                    section=section,
                    scope=document.scope,
                    domain=document.domain,
                    authority=document.authority,
                    source_path=document.source_path.as_posix(),
                    tags=document.tags,
                    chunk_index=index,
                    content=content,
                    content_sha256=_sha256(content),
                    document_sha256=document.document_sha256,
                    source_format=document.source_format,
                    page_numbers=page_numbers,
                )
            )
        return chunks

    def chunk_documents(self, documents: list[KnowledgeDocument]) -> list[KnowledgeChunk]:
        """批量切片并验证全语料 chunk_id 不重复。"""
        chunks: list[KnowledgeChunk] = []
        seen: set[str] = set()
        for document in documents:
            for chunk in self.chunk_document(document):
                if chunk.chunk_id in seen:
                    raise KnowledgeChunkingError(f"Duplicate chunk id: {chunk.chunk_id}")
                seen.add(chunk.chunk_id)
                chunks.append(chunk)
        return chunks
