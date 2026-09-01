"""JSONL Audit Reader（审计查询器）。

这是运维/合规内部能力，不暴露到公共 Agent API。
查询始终有 max_results 上限，避免一次性把整个审计文件加载进内存。
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any

from .writer import GovernedAuditWriter


class GovernedAuditReader:
    """读取当前受治理 JSONL Audit Store。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.writer = GovernedAuditWriter(self.root)
        self.path = self.writer.path

    def query(
        self,
        *,
        trace_id: str = "",
        tenant_id: str = "",
        subject: str = "",
        event_type: str = "",
        intent: str = "",
        runtime_status: str = "",
        since: str = "",
        max_results: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        """按结构化字段过滤，并返回最近的有限结果。"""

        if max_results < 1 or max_results > 1000:
            raise ValueError("max_results must stay within [1, 1000]")
        if not self.path.exists():
            return ()

        matches: deque[dict[str, Any]] = deque(maxlen=max_results)
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid audit JSONL at line {line_number}"
                    ) from exc

                if trace_id and row.get("trace_id") != trace_id:
                    continue
                if tenant_id and row.get("tenant_id") != tenant_id:
                    continue
                if subject and row.get("subject") != subject:
                    continue
                if event_type and row.get("event_type", "RUNTIME") != event_type:
                    continue
                if intent and row.get("intent") != intent:
                    continue
                if runtime_status and row.get("runtime_status") != runtime_status:
                    continue
                if since and str(row.get("occurred_at", "")) < since:
                    continue

                matches.append(row)

        return tuple(matches)
