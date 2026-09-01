"""API Admission / Timeout 的最小 Audit 投影。

这些事件发生在 Runtime Trace 形成之前，因此单独生成 trace_id 并写入同一个
append-only Audit Store。成功进入 Runtime 的请求继续使用 Runtime 自己的 Trace。

V1 暂不尝试把 API timeout event 与后台最终 Runtime trace 合并成一条记录；
两者都是可查询审计事实，但属于不同生命周期事件。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.audit import AgentAuditRecord, GovernedAuditWriter
from agent.tenancy import RequestContext


class GovernedAPIGuardAuditor:
    """把 Rate Limit / Concurrency / Timeout 投影为结构化 Audit Record。"""

    def __init__(
        self,
        project_root: Path | str,
        *,
        writer: GovernedAuditWriter | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.writer = writer or GovernedAuditWriter(self.root)

    @property
    def enabled(self) -> bool:
        """是否启用生产 Audit 持久化。"""

        return self.writer.enabled

    @property
    def fail_closed(self) -> bool:
        """Audit Store 不可写时是否禁止继续正常响应。"""

        return self.writer.fail_closed

    def record(
        self,
        *,
        trace_id: str,
        request_context: RequestContext,
        runtime_status: str,
        duration_ms: float = 0.0,
    ) -> None:
        """写入不含 Question/Answer/Bearer/JWT 原文的 API Guard Event。"""

        if not self.writer.enabled:
            return

        self.writer.write(
            AgentAuditRecord(
                schema_version=1,
                occurred_at=datetime.now(timezone.utc).isoformat(),
                trace_id=trace_id,
                tenant_id=str(request_context.tenant_id or ""),
                subject=str(request_context.subject or ""),
                intent="",
                route_status="NOT_STARTED",
                target_kind="",
                target_id="",
                authorization_status="AUTHENTICATED",
                runtime_status=runtime_status,
                answer_validated=False,
                stage_statuses=(
                    f"api_guard:{runtime_status}",
                ),
                duration_ms=duration_ms,
                estimated_context_tokens=0,
                tool_result_count=0,
                analysis_unit_attempts=0,
                retry_rounds=0,
                llm_calls=0,
                llm_total_tokens=0,
                llm_models=(),
                provider_cost_usd=None,
                cost_per_answer_usd=None,
                monetary_cost_known=False,
                event_type="API_GUARD",
            )
        )
