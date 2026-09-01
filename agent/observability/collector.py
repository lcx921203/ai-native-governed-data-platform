"""统一 Runtime 的 Trace / Usage / Cost Collector。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import yaml

from agent.llm import LLMUsageEvent

from .contracts import CostSummary, RunTrace
from .pricing import GovernedLLMPricing


class GovernedRunObserver:
    """从已有 Runtime Result + Provider Usage 聚合一次 Run 的成本单位。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/cost_observability_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        self.pricing = GovernedLLMPricing(self.root)

    def attach(
        self,
        result: Any,
        request_context: Any,
        *,
        total_duration_ms: float,
        llm_usage_events: Iterable[LLMUsageEvent] = (),
    ) -> Any:
        events = tuple(llm_usage_events)

        context_bundle = getattr(result, "context_bundle", None)
        estimated_context_tokens = int(
            getattr(context_bundle, "estimated_tokens", 0) or 0
        )

        execution = getattr(result, "execution", None)
        tool_result_count = len(getattr(execution, "results", ()) or ())

        analysis_execution = getattr(result, "analysis_execution", None)
        unit_results = tuple(
            getattr(analysis_execution, "unit_results", ()) or ()
        )
        analysis_unit_attempts = sum(
            max(0, int(getattr(item, "attempt", 0) or 0))
            for item in unit_results
        )
        retry_rounds = int(
            getattr(analysis_execution, "retry_rounds", 0) or 0
        )

        pricing = self.pricing.price(events)
        provider_cost = pricing.total_cost_usd if pricing.known else None

        # Cost per Answer 只在最终 Draft 通过 Answer Validator 后成立。
        cost_per_answer = (
            provider_cost
            if provider_cost is not None
            and bool(getattr(result, "answer_validated", False))
            else None
        )

        cost = CostSummary(
            total_duration_ms=total_duration_ms,
            estimated_context_tokens=estimated_context_tokens,
            tool_result_count=tool_result_count,
            analysis_unit_attempts=analysis_unit_attempts,
            retry_rounds=retry_rounds,
            llm_calls=len(events),
            llm_input_tokens=sum(max(0, e.input_tokens) for e in events),
            llm_cached_input_tokens=sum(
                max(0, e.cached_input_tokens) for e in events
            ),
            llm_cache_write_tokens=sum(
                max(0, e.cache_write_tokens) for e in events
            ),
            llm_output_tokens=sum(max(0, e.output_tokens) for e in events),
            llm_reasoning_tokens=sum(
                max(0, e.reasoning_tokens) for e in events
            ),
            llm_total_tokens=sum(max(0, e.total_tokens) for e in events),
            llm_models=tuple(dict.fromkeys(e.model for e in events)),
            provider_cost_usd=provider_cost,
            cost_per_answer_usd=cost_per_answer,
            monetary_cost_known=pricing.known,
            pricing_breakdown=tuple(pricing.breakdown),
            pricing_warnings=tuple(pricing.warnings),
        )

        stages = tuple(
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in getattr(result, "stage_trace", ()) or ()
        )
        trace = RunTrace(
            trace_id=str(uuid4()),
            tenant_id=str(getattr(request_context, "tenant_id", "") or ""),
            subject=str(getattr(request_context, "subject", "") or ""),
            status=str(
                getattr(
                    getattr(result, "status", None),
                    "value",
                    getattr(result, "status", ""),
                )
            ),
            answer_validated=bool(getattr(result, "answer_validated", False)),
            stages=stages,
            cost=cost,
        )

        result.request_context = request_context
        result.observability = trace
        return result
