"""Agent Runtime 的 Trace / Cost Observability 契约。

V2 在 V1 的 Context/Tool/Retry 观测基础上加入 Provider 实际 Usage：
- input / cached input / cache-write / output / reasoning token；
- provider/model；
- 受治理 pricing catalog 得出的 Provider Cost；
- 未记录 usage、未知 model 或长上下文时，Monetary Cost 保持 unknown。
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
    llm_cached_input_tokens: int = 0
    llm_cache_write_tokens: int = 0
    llm_output_tokens: int = 0
    llm_reasoning_tokens: int = 0
    llm_total_tokens: int = 0
    llm_models: tuple[str, ...] = ()

    provider_cost_usd: float | None = None
    cost_per_answer_usd: float | None = None
    monetary_cost_known: bool = False
    pricing_breakdown: tuple[dict[str, Any], ...] = ()
    pricing_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_duration_ms": round(self.total_duration_ms, 3),
            "estimated_context_tokens": self.estimated_context_tokens,
            "tool_result_count": self.tool_result_count,
            "analysis_unit_attempts": self.analysis_unit_attempts,
            "retry_rounds": self.retry_rounds,
            "llm_calls": self.llm_calls,
            "llm_input_tokens": self.llm_input_tokens,
            "llm_cached_input_tokens": self.llm_cached_input_tokens,
            "llm_cache_write_tokens": self.llm_cache_write_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "llm_reasoning_tokens": self.llm_reasoning_tokens,
            "llm_total_tokens": self.llm_total_tokens,
            "llm_models": list(self.llm_models),
            "provider_cost_usd": self.provider_cost_usd,
            "cost_per_answer_usd": self.cost_per_answer_usd,
            "monetary_cost_known": self.monetary_cost_known,
            "pricing_breakdown": [dict(item) for item in self.pricing_breakdown],
            "pricing_warnings": list(self.pricing_warnings),
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
