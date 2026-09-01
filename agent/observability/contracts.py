"""Agent Runtime 的 Trace / Cost Observability 契约。

当前 Unified Runtime 仍是 deterministic renderer，没有真正 LLM Provider 调用，
所以绝不伪造 token price 或美元成本。

V1 先稳定记录：
- tenant / subject（不含 token）；
- total latency；
- Context estimated tokens；
- Tool result 数；
- Analysis unit attempts / retry rounds；
- final answer status / validation；
- cost_per_answer_usd = None，直到真实 Provider/Trino 等 usage 被接入。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CostSummary:
    total_duration_ms: float
    estimated_context_tokens: int
    tool_result_count: int
    analysis_unit_attempts: int
    retry_rounds: int

    llm_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    provider_cost_usd: float | None = None
    cost_per_answer_usd: float | None = None
    monetary_cost_known: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_duration_ms": round(self.total_duration_ms, 3),
            "estimated_context_tokens": self.estimated_context_tokens,
            "tool_result_count": self.tool_result_count,
            "analysis_unit_attempts": self.analysis_unit_attempts,
            "retry_rounds": self.retry_rounds,
            "llm_calls": self.llm_calls,
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "provider_cost_usd": self.provider_cost_usd,
            "cost_per_answer_usd": self.cost_per_answer_usd,
            "monetary_cost_known": self.monetary_cost_known,
        }


@dataclass(frozen=True)
class RunTrace:
    trace_id: str
    tenant_id: str
    subject: str
    status: str
    answer_validated: bool
    stages: tuple[dict[str, Any], ...]
    cost: CostSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "tenant_id": self.tenant_id,
            "subject": self.subject,
            "status": self.status,
            "answer_validated": self.answer_validated,
            "stages": [dict(item) for item in self.stages],
            "cost": self.cost.to_dict(),
        }
