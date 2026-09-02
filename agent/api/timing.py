"""Agent API Outside-Runtime Timing（运行时外部耗时）内部观测。

目标：把 Authenticated Agent API 的 HTTP Total 拆成可解释的受治理阶段：

    Client HTTP Total
      -> JWT Verification
      -> RequestContext Mapping
      -> Shared Admission
      -> ThreadPool Queue
      -> GovernedAgentRuntime Core
      -> Runtime Observer / Audit Persistence
      -> Lease Release
      -> Response Model Build
      -> FastAPI / ASGI Residual
      -> Client/Transport Residual

边界：
- Timing 只保存固定阶段名 + duration_ms；
- 不保存 Prompt、Answer、Bearer Token、JWT Claims、Redis URL 或内部 Payload；
- Public API 不返回 Timing Header / Timing Body；
- 详细 API Timing 默认关闭，仅在受控诊断 / SLO Calibration 时启用；
- Timing Audit 写入发生在 HTTP Response 已经发送之后，不进入 Server Total。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml
from starlette.concurrency import run_in_threadpool

from agent.audit import AgentAuditRecord, GovernedAuditWriter
from agent.tenancy import RequestContext


_LOGGER = logging.getLogger("agent.api.timing")
_STATE_TIMING_KEY = "agent_api_timing_trace"
_STATE_CONTEXT_KEY = "agent_api_request_context"
_PENDING_WRITES: set[asyncio.Task] = set()


def _timing_write_done(task: asyncio.Task) -> None:
    """回收后台 Timing Audit Task，并只记录不含异常正文的固定告警。"""

    _PENDING_WRITES.discard(task)
    try:
        task.result()
    except Exception:
        _LOGGER.warning(
            "Agent API timing audit persistence failed."
        )


@dataclass
class APITimingTrace:
    """一次 HTTP Request 的固定阶段耗时累加器。"""

    allowed_phases: frozenset[str]
    phases: dict[str, float] = field(default_factory=dict)

    def add(self, phase: str, duration_ms: float) -> None:
        """累加一个 bounded phase；未知 Label 直接拒绝。"""

        if phase not in self.allowed_phases:
            raise ValueError(f"Unsupported Agent API timing phase: {phase!r}")
        self.phases[phase] = self.phases.get(phase, 0.0) + max(
            0.0,
            float(duration_ms),
        )

    def as_tuple(self) -> tuple[tuple[str, float], ...]:
        """按名称稳定排序，便于 Audit / Evidence 做确定性聚合。"""

        return tuple(
            (name, self.phases[name])
            for name in sorted(self.phases)
        )


class GovernedAPITimingAuditor:
    """把 API Phase Timing 投影到既有 append-only Audit Store。"""

    def __init__(
        self,
        project_root: Path | str,
        *,
        writer: GovernedAuditWriter | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (
                self.root
                / "agent/contracts/agent_api_timing_policy.yml"
            ).read_text(encoding="utf-8")
        )
        runtime = self.policy["runtime"]
        self.mode = os.getenv(
            str(runtime["mode_env"]),
            str(runtime["default_mode"]),
        ).strip().lower()
        allowed_modes = {
            str(item)
            for item in runtime["allowed_modes"]
        }
        if self.mode not in allowed_modes:
            raise ValueError(
                f"Unsupported API timing mode={self.mode!r}; "
                f"allowed={sorted(allowed_modes)}"
            )

        self.allowed_phases = frozenset(
            str(item)
            for item in self.policy["phases"]
        )
        self.writer = writer or GovernedAuditWriter(self.root)

    @property
    def enabled(self) -> bool:
        """只有 Timing Mode 与 Core Audit 都开启时才持久化诊断记录。"""

        return self.mode == "audit" and self.writer.enabled

    def new_trace(self) -> APITimingTrace:
        """为一次 Request 创建只接受固定 Label 的 Timing Trace。"""

        return APITimingTrace(
            allowed_phases=self.allowed_phases
        )

    def build_record(
        self,
        *,
        trace_id: str,
        request_context: RequestContext,
        http_status: int,
        server_total_ms: float,
        phase_timings: tuple[tuple[str, float], ...],
    ) -> AgentAuditRecord:
        """构造不含业务自由文本的 API_TIMING Audit Record。"""

        return AgentAuditRecord(
            schema_version=1,
            occurred_at=datetime.now(timezone.utc).isoformat(),
            trace_id=trace_id,
            tenant_id=str(request_context.tenant_id or ""),
            subject=str(request_context.subject or ""),
            intent="",
            route_status="API_TIMING_ONLY",
            target_kind="",
            target_id="",
            authorization_status="AUTHENTICATED",
            runtime_status=f"HTTP_{int(http_status)}",
            answer_validated=False,
            stage_statuses=("api_timing:OBSERVED",),
            stage_timings=phase_timings,
            duration_ms=max(0.0, float(server_total_ms)),
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
            event_type="API_TIMING",
        )

    def write_record(self, record: AgentAuditRecord) -> None:
        """写入一条 Timing Audit；详细观测失败不改写已经发送的 HTTP Response。"""

        if not self.enabled:
            return
        self.writer.write(record)


class GovernedAPITimingMiddleware:
    """纯 ASGI Middleware：测量 Server Total，并在 Response 发送后写 Timing Audit。"""

    def __init__(
        self,
        app: Any,
        *,
        project_root: Path | str,
    ):
        self.app = app
        self.auditor = GovernedAPITimingAuditor(project_root)

    async def __call__(self, scope, receive, send) -> None:
        """只观测 HTTP Request；WebSocket / Lifespan 原样透传。"""

        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        timing = self.auditor.new_trace()
        state[_STATE_TIMING_KEY] = timing

        started = perf_counter()
        response_status = 0
        trace_id = ""

        async def send_wrapper(message) -> None:
            """捕获 Response Status / Trace Header，但不修改公共响应。"""

            nonlocal response_status, trace_id
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status") or 0)
                for key, value in message.get("headers") or []:
                    if bytes(key).lower() == b"x-trace-id":
                        trace_id = bytes(value).decode(
                            "latin-1",
                            errors="ignore",
                        )
                        break
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            server_total_ms = max(
                0.0,
                (perf_counter() - started) * 1000,
            )
            request_context = state.get(_STATE_CONTEXT_KEY)

            if (
                self.auditor.enabled
                and trace_id
                and isinstance(request_context, RequestContext)
            ):
                record = self.auditor.build_record(
                    trace_id=trace_id,
                    request_context=request_context,
                    http_status=response_status,
                    server_total_ms=server_total_ms,
                    phase_timings=timing.as_tuple(),
                )
                # Response Body 已经发送；异步调度 Audit fsync 后立即结束当前 ASGI Request。
                # 这样同一 HTTP/1.1 Connection 的下一次请求不会被诊断写盘串行阻塞。
                write_task = asyncio.create_task(
                    run_in_threadpool(
                        self.auditor.write_record,
                        record,
                    )
                )
                _PENDING_WRITES.add(write_task)
                write_task.add_done_callback(
                    _timing_write_done
                )


def record_api_phase(
    request: Any,
    phase: str,
    duration_ms: float,
) -> None:
    """把固定阶段耗时写入当前 Request State；Timing 未初始化时安全跳过。"""

    trace = getattr(
        getattr(request, "state", None),
        _STATE_TIMING_KEY,
        None,
    )
    if isinstance(trace, APITimingTrace):
        trace.add(
            phase,
            duration_ms,
        )


def bind_api_request_context(
    request: Any,
    request_context: RequestContext,
) -> None:
    """仅把已经验证的最小 RequestContext 绑定到 Request State，供内部 Timing Audit 关联。"""

    state = getattr(request, "state", None)
    if state is not None:
        setattr(
            state,
            _STATE_CONTEXT_KEY,
            request_context,
        )
