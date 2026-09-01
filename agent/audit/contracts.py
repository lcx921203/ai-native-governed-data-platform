"""Agent Runtime / API Guard Audit（审计）的结构化契约。

审计记录只保留“谁以什么受治理身份执行了什么类型的能力，以及结果/成本如何”。
明确不保存：
- 原始 question / prompt；
- answer 文本；
- Bearer Token / JWT；
- Provider 原始 response；
- 内部 warning/error 详情。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentAuditRecord:
    """一次 Runtime 或 API Guard 生命周期事件的最小不可逆审计投影。"""

    schema_version: int
    occurred_at: str
    trace_id: str

    tenant_id: str
    subject: str

    intent: str
    route_status: str
    target_kind: str
    target_id: str

    authorization_status: str
    runtime_status: str
    answer_validated: bool
    stage_statuses: tuple[str, ...]

    duration_ms: float
    estimated_context_tokens: int
    tool_result_count: int
    analysis_unit_attempts: int
    retry_rounds: int

    llm_calls: int
    llm_total_tokens: int
    llm_models: tuple[str, ...]
    provider_cost_usd: float | None
    cost_per_answer_usd: float | None
    monetary_cost_known: bool

    # 向后兼容：旧 Runtime 构造代码不传该字段时仍默认为 RUNTIME。
    event_type: str = "RUNTIME"

    def to_dict(self) -> dict[str, Any]:
        """输出 JSONL 可序列化结构；不加入自由文本字段。"""

        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "trace_id": self.trace_id,
            "tenant_id": self.tenant_id,
            "subject": self.subject,
            "intent": self.intent,
            "route_status": self.route_status,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "authorization_status": self.authorization_status,
            "runtime_status": self.runtime_status,
            "answer_validated": self.answer_validated,
            "stage_statuses": list(self.stage_statuses),
            "duration_ms": round(self.duration_ms, 3),
            "estimated_context_tokens": self.estimated_context_tokens,
            "tool_result_count": self.tool_result_count,
            "analysis_unit_attempts": self.analysis_unit_attempts,
            "retry_rounds": self.retry_rounds,
            "llm_calls": self.llm_calls,
            "llm_total_tokens": self.llm_total_tokens,
            "llm_models": list(self.llm_models),
            "provider_cost_usd": self.provider_cost_usd,
            "cost_per_answer_usd": self.cost_per_answer_usd,
            "monetary_cost_known": self.monetary_cost_known,
        }
