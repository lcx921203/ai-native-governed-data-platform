"""统一 Runtime 的 Trace / Usage Collector。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .contracts import CostSummary, RunTrace


class GovernedRunObserver:
    """从已有 Runtime Result 聚合成本单位，不猜 Provider 价格。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/cost_observability_policy.yml").read_text(
                encoding="utf-8"
            )
        )

    def attach(
        self,
        result: Any,
        request_context: Any,
        *,
        total_duration_ms: float,
    ) -> Any:
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
        # attempt=2 代表该 Unit 实际执行了两次，因此求和可以反映 bounded retry 的执行量。
        analysis_unit_attempts = sum(
            max(0, int(getattr(item, "attempt", 0) or 0))
            for item in unit_results
        )
        retry_rounds = int(
            getattr(analysis_execution, "retry_rounds", 0) or 0
        )

        # 当前 Unified Runtime 仍使用 deterministic renderer。
        # 未接 Provider Usage 前，美元成本必须保持 unknown。
        cost = CostSummary(
            total_duration_ms=total_duration_ms,
            estimated_context_tokens=estimated_context_tokens,
            tool_result_count=tool_result_count,
            analysis_unit_attempts=analysis_unit_attempts,
            retry_rounds=retry_rounds,
            provider_cost_usd=None,
            cost_per_answer_usd=None,
            monetary_cost_known=False,
        )

        stages = tuple(
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
            for item in getattr(result, "stage_trace", ()) or ()
        )
        trace = RunTrace(
            trace_id=str(uuid4()),
            tenant_id=str(getattr(request_context, "tenant_id", "") or ""),
            subject=str(getattr(request_context, "subject", "") or ""),
            status=str(getattr(getattr(result, "status", None), "value", getattr(result, "status", ""))),
            answer_validated=bool(getattr(result, "answer_validated", False)),
            stages=stages,
            cost=cost,
        )

        result.request_context = request_context
        result.observability = trace
        return result
