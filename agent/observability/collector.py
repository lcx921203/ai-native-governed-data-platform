"""统一 Runtime 的 Trace / Usage / Cost / Audit Collector。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import yaml

from agent.audit import AgentAuditRecord, AuditWriteError, GovernedAuditWriter
from agent.llm import LLMUsageEvent

from .contracts import CostSummary, RunTrace
from .pricing import GovernedLLMPricing


class GovernedRunObserver:
    """聚合一次 Run 的 Trace、Provider Usage、Cost，并写入受治理 Audit。"""

    def __init__(
        self,
        project_root: Path | str,
        *,
        audit_writer: GovernedAuditWriter | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/cost_observability_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        self.pricing = GovernedLLMPricing(self.root)
        self.audit_writer = audit_writer or GovernedAuditWriter(self.root)

    def attach(
        self,
        result: Any,
        request_context: Any,
        *,
        total_duration_ms: float,
        llm_usage_events: Iterable[LLMUsageEvent] = (),
    ) -> Any:
        """把运行观测附加到 Result，并在启用时追加最小 Audit Record。"""

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
            status=self._status_value(result),
            answer_validated=bool(getattr(result, "answer_validated", False)),
            stages=stages,
            cost=cost,
            audit_status=(
                "PENDING" if self.audit_writer.enabled else "DISABLED"
            ),
        )

        result.request_context = request_context
        result.observability = trace

        if not self.audit_writer.enabled:
            return result

        try:
            self.audit_writer.write(
                self._audit_record(
                    result,
                    request_context,
                    trace,
                )
            )
        except AuditWriteError:
            failed = replace(
                trace,
                audit_status="FAILED",
            )
            result.observability = failed
            if self.audit_writer.fail_closed:
                self._fail_closed(result)
            return result

        result.observability = replace(
            trace,
            audit_status="WRITTEN",
        )
        return result

    def _audit_record(
        self,
        result: Any,
        request_context: Any,
        trace: RunTrace,
    ) -> AgentAuditRecord:
        """从 Runtime Result 构造不含 Prompt/Answer/Token 的 Audit Record。"""

        route = getattr(result, "route", None)
        intent = self._enum_value(getattr(route, "intent", ""))
        route_status = self._enum_value(getattr(route, "status", ""))

        authorization_status = "NOT_APPLICABLE"
        for stage in getattr(result, "stage_trace", ()) or ():
            if getattr(stage, "stage", "") == "authorization":
                authorization_status = str(
                    getattr(stage, "status", "") or ""
                )
                break

        return AgentAuditRecord(
            schema_version=1,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            trace_id=trace.trace_id,
            tenant_id=str(
                getattr(request_context, "tenant_id", "") or ""
            ),
            subject=str(
                getattr(request_context, "subject", "") or ""
            ),
            intent=intent,
            route_status=route_status,
            target_kind=str(
                getattr(route, "target_kind", "") or ""
            ),
            target_id=str(
                getattr(route, "target_id", "") or ""
            ),
            authorization_status=authorization_status,
            runtime_status=self._status_value(result),
            answer_validated=bool(
                getattr(result, "answer_validated", False)
            ),
            stage_statuses=tuple(
                f"{getattr(stage, 'stage', '')}:{getattr(stage, 'status', '')}"
                for stage in getattr(result, "stage_trace", ()) or ()
            ),
            duration_ms=trace.cost.total_duration_ms,
            estimated_context_tokens=trace.cost.estimated_context_tokens,
            tool_result_count=trace.cost.tool_result_count,
            analysis_unit_attempts=trace.cost.analysis_unit_attempts,
            retry_rounds=trace.cost.retry_rounds,
            llm_calls=trace.cost.llm_calls,
            llm_total_tokens=trace.cost.llm_total_tokens,
            llm_models=trace.cost.llm_models,
            provider_cost_usd=trace.cost.provider_cost_usd,
            cost_per_answer_usd=trace.cost.cost_per_answer_usd,
            monetary_cost_known=trace.cost.monetary_cost_known,
        )

    @staticmethod
    def _fail_closed(result: Any) -> None:
        """Audit 落盘失败时撤销可返回答案，保留 Trace ID 供 API 报错关联。"""

        current = getattr(result, "status", None)
        enum_type = type(current)
        if hasattr(enum_type, "ERROR"):
            result.status = enum_type.ERROR
        result.answer_validated = False
        result.draft = None
        warnings = list(getattr(result, "warnings", ()) or ())
        warnings.append(
            "Audit persistence failed; the answer was withheld."
        )
        result.warnings = warnings

    @staticmethod
    def _enum_value(value: Any) -> str:
        """把 Enum 或普通值统一转换为字符串。"""

        return str(getattr(value, "value", value) or "")

    @classmethod
    def _status_value(cls, result: Any) -> str:
        """读取 Runtime Result 的最终状态字符串。"""

        return cls._enum_value(getattr(result, "status", ""))
