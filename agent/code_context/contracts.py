"""Model Context Card（模型上下文卡）的结构化契约。

目标：
- 把 dbt SQL / YAML 中与理解模型有关的事实压缩成小型 Context Card；
- Card 是 Code 的索引和压缩，不是新的 Source of Truth（事实源）；
- 每张 Card 都带源文件 Git-blob 指纹，源代码变化后旧 Card 立即失效；
- 原始源码只允许作为显式、有限行数的 fallback（兜底），不能默认整文件进入 LLM Context。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelContextStatus(str, Enum):
    """Code Context 解析状态。"""

    RESOLVED = "RESOLVED"
    STALE = "STALE"
    NOT_FOUND = "NOT_FOUND"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class SourceFingerprint:
    """Card 所依据的一个源文件指纹。"""

    path: str
    git_blob_sha: str
    authority: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "git_blob_sha": self.git_blob_sha,
            "authority": self.authority,
        }


@dataclass(frozen=True)
class ModelContextCard:
    """从代码和受治理 YAML 提炼出的紧凑模型上下文。"""

    version: int
    model: str
    description: str
    grain: str | None
    source_sql: str
    source_fingerprints: tuple[SourceFingerprint, ...]
    config: dict[str, Any]
    upstream_refs: tuple[str, ...]
    upstream_sources: tuple[str, ...]
    execution_window_fields: tuple[str, ...]
    macros: tuple[str, ...]
    join_signals: tuple[str, ...]
    predicate_signals: tuple[str, ...]
    business_time: str | None
    entities: tuple[dict[str, Any], ...]
    dimensions: tuple[dict[str, Any], ...]
    metrics: tuple[dict[str, Any], ...]
    card_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model": self.model,
            "description": self.description,
            "grain": self.grain,
            "source_sql": self.source_sql,
            "source_fingerprints": [item.to_dict() for item in self.source_fingerprints],
            "config": dict(self.config),
            "upstream_refs": list(self.upstream_refs),
            "upstream_sources": list(self.upstream_sources),
            "execution_window_fields": list(self.execution_window_fields),
            "macros": list(self.macros),
            "join_signals": list(self.join_signals),
            "predicate_signals": list(self.predicate_signals),
            "business_time": self.business_time,
            "entities": [dict(item) for item in self.entities],
            "dimensions": [dict(item) for item in self.dimensions],
            "metrics": [dict(item) for item in self.metrics],
            "card_fingerprint": self.card_fingerprint,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModelContextCard":
        return cls(
            version=int(raw["version"]),
            model=str(raw["model"]),
            description=str(raw.get("description", "")),
            grain=raw.get("grain"),
            source_sql=str(raw["source_sql"]),
            source_fingerprints=tuple(
                SourceFingerprint(
                    path=str(item["path"]),
                    git_blob_sha=str(item["git_blob_sha"]),
                    authority=str(item["authority"]),
                )
                for item in raw.get("source_fingerprints", ())
            ),
            config=dict(raw.get("config") or {}),
            upstream_refs=tuple(str(x) for x in raw.get("upstream_refs", ())),
            upstream_sources=tuple(str(x) for x in raw.get("upstream_sources", ())),
            execution_window_fields=tuple(str(x) for x in raw.get("execution_window_fields", ())),
            macros=tuple(str(x) for x in raw.get("macros", ())),
            join_signals=tuple(str(x) for x in raw.get("join_signals", ())),
            predicate_signals=tuple(str(x) for x in raw.get("predicate_signals", ())),
            business_time=raw.get("business_time"),
            entities=tuple(dict(x) for x in raw.get("entities", ())),
            dimensions=tuple(dict(x) for x in raw.get("dimensions", ())),
            metrics=tuple(dict(x) for x in raw.get("metrics", ())),
            card_fingerprint=str(raw.get("card_fingerprint", "")),
        )


@dataclass(frozen=True)
class ModelContextResolution:
    """Model Context Repository 的读取结果。"""

    status: ModelContextStatus
    model: str
    card: ModelContextCard | None = None
    evidence_mode: str = ""
    estimated_tokens: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "model": self.model,
            "card": self.card.to_dict() if self.card else None,
            "evidence_mode": self.evidence_mode,
            "estimated_tokens": self.estimated_tokens,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class RawCodeSnippet:
    """显式 Raw Code fallback 的有限源码片段。"""

    status: ModelContextStatus
    model: str
    source_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    content: str = ""
    estimated_tokens: int = 0
    warnings: tuple[str, ...] = ()
