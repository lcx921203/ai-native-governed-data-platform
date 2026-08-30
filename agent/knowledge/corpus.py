"""受治理 Knowledge Corpus（知识语料库）加载器。

业务逻辑：只有 ``corpus_manifest.yml`` 明确登记的文档才允许进入知识库；
不同格式先经过固定 Parser Registry 解析，再统一成 ``KnowledgeDocument``。

当前治理规则：
- Markdown 继续要求 YAML Front Matter，并与 Manifest 交叉校验；
- PDF / DOCX 没有 YAML Front Matter，因此治理元数据必须完整写在 Manifest；
- 所有路径都必须位于 ``knowledge/`` 根目录，禁止路径逃逸；
- status!=active 的文档不进入索引。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from agent.knowledge.document_ingestion import KnowledgeDocumentParserRegistry
from agent.knowledge.models import KnowledgeDocument

REQUIRED_FRONT_MATTER = {"id", "title", "scope", "domain", "authority", "owner", "status"}
REQUIRED_BINARY_MANIFEST = {"id", "path", "title", "scope", "domain", "authority", "owner", "status"}
ALLOWED_AUTHORITIES = {"normative", "explanatory", "runbook", "design_decision", "reference"}


class KnowledgeCorpusError(RuntimeError):
    """知识语料违反 Manifest / Front Matter / 路径边界时抛出的契约错误。"""


def sha256_text(value: str) -> str:
    """对 UTF-8 文本计算 SHA-256，用于固定文本型文档或切片的内容身份。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """对源文件原始字节计算 SHA-256，PDF / DOCX 等二进制文档也能稳定追踪版本。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_front_matter(raw: str) -> tuple[dict[str, Any], str]:
    """拆分 Markdown YAML Front Matter 与正文。

    输入是一整份 Markdown；输出 ``(metadata, body)``。如果缺少开头/结尾 ``---``，
    立即 Fail Closed，而不是把一份无治理元数据的普通文件偷偷送进 RAG。
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise KnowledgeCorpusError("Knowledge document must start with YAML front matter.")
    try:
        closing = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise KnowledgeCorpusError("Knowledge document front matter is not closed.") from exc
    return yaml.safe_load("\n".join(lines[1:closing])) or {}, "\n".join(lines[closing + 1 :]).strip()


class KnowledgeCorpus:
    """只从固定知识根目录与 Manifest 构建受治理、多格式文档集合。"""

    def __init__(self, project_root: Path | str, *, parser_registry: KnowledgeDocumentParserRegistry | None = None):
        """绑定工程根目录、``knowledge/`` 根目录、Manifest 与固定 Parser Registry。"""
        self.root = Path(project_root).resolve()
        self.knowledge_root = (self.root / "knowledge").resolve()
        self.manifest_path = self.root / "metadata/knowledge/corpus_manifest.yml"
        self.parsers = parser_registry or KnowledgeDocumentParserRegistry()

    def _resolve_path(self, item: dict[str, Any]) -> Path:
        """把 Manifest path 解析为受治理绝对路径，并拒绝逃逸 ``knowledge/``。"""
        path = (self.root / item["path"]).resolve()
        try:
            path.relative_to(self.knowledge_root)
        except ValueError as exc:
            raise KnowledgeCorpusError(f"Document escapes knowledge root: {path}") from exc
        if not path.is_file():
            raise KnowledgeCorpusError(f"Manifest document does not exist: {path}")
        return path

    @staticmethod
    def _validate_authority(metadata: dict[str, Any], path: Path) -> None:
        """校验 knowledge authority 白名单，防止任意自定义 authority 绕过 Claim Matrix。"""
        if metadata["authority"] not in ALLOWED_AUTHORITIES:
            raise KnowledgeCorpusError(f"{path}: invalid authority")

    def _load_markdown(self, item: dict[str, Any], path: Path) -> KnowledgeDocument | None:
        """加载 Markdown，并要求 Front Matter 与 Manifest 的 id/scope 一致。

        Markdown 仍保留当前 18 份知识文档的既有治理方式，因此不会因为新增 PDF / DOCX
        能力而改变已有 chunk identity。
        """
        parsed = self.parsers.parse(path)
        metadata, body = parse_front_matter(parsed.body)
        missing = REQUIRED_FRONT_MATTER - metadata.keys()
        if missing:
            raise KnowledgeCorpusError(f"{path}: missing front matter {sorted(missing)}")
        if metadata["id"] != item["id"] or metadata["scope"] != item["scope"]:
            raise KnowledgeCorpusError(f"{path}: manifest/front-matter mismatch")
        self._validate_authority(metadata, path)
        if metadata["status"] != "active":
            return None
        return KnowledgeDocument(
            document_id=item["id"],
            title=metadata["title"],
            scope=metadata["scope"],
            domain=metadata["domain"],
            authority=metadata["authority"],
            owner=metadata["owner"],
            status=metadata["status"],
            tags=tuple(metadata.get("tags", [])),
            source_path=path.relative_to(self.root),
            reviewed_at=metadata.get("reviewed_at"),
            body=body,
            document_sha256=sha256_file(path),
            source_format=parsed.source_format,
            blocks=(),
        )

    def _load_binary(self, item: dict[str, Any], path: Path) -> KnowledgeDocument | None:
        """加载 PDF / DOCX，并从 Manifest 获取完整治理元数据。

        二进制文件本身没有 Markdown Front Matter，因此 Manifest 是它们的 identity / owner /
        scope / authority Source Authority。Parser 只负责内容结构，不拥有治理决策权。
        """
        missing = REQUIRED_BINARY_MANIFEST - item.keys()
        if missing:
            raise KnowledgeCorpusError(
                f"{path}: PDF/DOCX manifest metadata incomplete; missing {sorted(missing)}"
            )
        self._validate_authority(item, path)
        if item["status"] != "active":
            return None
        parsed = self.parsers.parse(path)
        return KnowledgeDocument(
            document_id=item["id"],
            title=item["title"],
            scope=item["scope"],
            domain=item["domain"],
            authority=item["authority"],
            owner=item["owner"],
            status=item["status"],
            tags=tuple(item.get("tags", [])),
            source_path=path.relative_to(self.root),
            reviewed_at=item.get("reviewed_at"),
            body=parsed.body,
            document_sha256=sha256_file(path),
            source_format=parsed.source_format,
            blocks=parsed.blocks,
        )

    def load(self) -> list[KnowledgeDocument]:
        """读取并校验 Manifest 中所有 active 文档。

        输出统一 ``KnowledgeDocument``：当前 active corpus 仍是 18 份 Markdown；
        PDF / DOCX Parser 与统一 Contract 已 SOURCE DEFINED，但是否有真实企业文档接入仍取决于
        Manifest 中未来登记的实际文件，不能把“Parser 存在”包装成“企业文档已 ingest”。
        """
        manifest = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        documents: list[KnowledgeDocument] = []
        seen: set[str] = set()
        for item in manifest["documents"]:
            document_id = item["id"]
            if document_id in seen:
                raise KnowledgeCorpusError(f"Duplicate document id: {document_id}")
            seen.add(document_id)
            path = self._resolve_path(item)
            suffix = path.suffix.casefold()
            if suffix in {".md", ".markdown"}:
                document = self._load_markdown(item, path)
            elif suffix in {".pdf", ".docx"}:
                document = self._load_binary(item, path)
            else:
                # Registry 自己也会拒绝，但这里提前给出更贴近 Corpus Contract 的错误。
                raise KnowledgeCorpusError(
                    f"{path}: unsupported format; allowed={self.parsers.supported_suffixes}"
                )
            if document is not None:
                documents.append(document)
        return documents
