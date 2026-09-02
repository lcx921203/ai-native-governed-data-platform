"""Production Agent API + Trusted Identity + Distributed SLO Guard。

HTTP 链路：
    Bearer JWT
      -> Verified RequestContext
      -> Local / Redis Shared Admission
      -> Subject/Tenant Rate Limit
      -> Tenant/Global Concurrency Lease
      -> GovernedAgentRuntime
      -> Timeout Boundary
      -> Answer Validator
      -> minimal public response

Redis Backend 可以跨 Worker / Pod 共享容量；Local Backend 仍只适合单进程。
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import yaml
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    Security,
)
from fastapi.responses import JSONResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from starlette.concurrency import run_in_threadpool

from agent.audit import AuditWriteError
from agent.runtime import build_runtime_from_env
from agent.tenancy import RequestContext
from mcp_server.auth.jwt import (
    JWKSJWTVerifier,
    JWTVerificationError,
)

from .auth import (
    AgentAPIIdentityError,
    AgentIdentityMapper,
)
from .contracts import (
    AgentQueryRequest,
    AgentQueryResponse,
    HealthResponse,
)
from .guard_audit import (
    GovernedAPIGuardAuditor,
)
from .traffic import (
    AdmissionRejected,
    TrafficGuardConfigurationError,
    TrafficGuardUnavailable,
    build_traffic_guard_from_env,
)
from .timing import (
    GovernedAPITimingMiddleware,
    bind_api_request_context,
    record_api_metric,
    record_api_phase,
)


ROOT = Path(__file__).resolve().parents[2]
_BEARER = HTTPBearer(auto_error=False)
_LOGGER = logging.getLogger(
    "agent.api.traffic"
)


class AgentAPIConfigurationError(RuntimeError):
    """Agent API 的认证或运行配置不完整。"""


@lru_cache(maxsize=1)
def get_api_policy() -> dict[str, Any]:
    """读取 Agent API 的仓库治理策略。"""

    return yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_api_policy.yml"
        ).read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def get_runtime():
    """构造进程级 Single Agent Runtime；Renderer Gate 保持不变。"""

    return build_runtime_from_env(
        ROOT
    )


@lru_cache(maxsize=1)
def get_identity_mapper() -> AgentIdentityMapper:
    """构造进程级 JWT claims -> RequestContext Mapper。"""

    return AgentIdentityMapper(
        ROOT
    )


@lru_cache(maxsize=1)
def get_traffic_guard():
    """按 AGENT_API_TRAFFIC_BACKEND 构造 Local 或 Redis Shared Guard。"""

    return build_traffic_guard_from_env(
        ROOT
    )


@lru_cache(maxsize=1)
def get_guard_auditor() -> GovernedAPIGuardAuditor:
    """构造 API Guard Event 审计器。"""

    return GovernedAPIGuardAuditor(
        ROOT
    )


@lru_cache(maxsize=1)
def get_jwt_verifier() -> JWKSJWTVerifier:
    """读取 Agent API 专属 JWKS 配置并复用现有 JWT 验证实现。"""

    config = get_api_policy()[
        "authentication"
    ]["jwt"]
    jwks_url = os.getenv(
        str(config["jwks_url_env"]),
        "",
    ).strip()
    issuer = os.getenv(
        str(config["issuer_env"]),
        "",
    ).strip()
    audience = os.getenv(
        str(config["audience_env"]),
        "",
    ).strip()

    if not (
        jwks_url
        and issuer
        and audience
    ):
        raise AgentAPIConfigurationError(
            "Agent API JWT verifier is not configured."
        )

    return JWKSJWTVerifier(
        jwks_url=jwks_url,
        issuer=issuer,
        audience=audience,
        algorithms=tuple(
            str(item)
            for item in config[
                "algorithms"
            ]
        ),
    )


def get_request_context(
    request: Request,
    credentials: (
        HTTPAuthorizationCredentials
        | None
    ) = Security(_BEARER),
    verifier: JWKSJWTVerifier = Depends(
        get_jwt_verifier
    ),
    mapper: AgentIdentityMapper = Depends(
        get_identity_mapper
    ),
) -> RequestContext:
    """验证 Bearer JWT，并记录不含 Token/Claims 的认证阶段耗时。"""

    if (
        credentials is None
        or credentials.scheme.lower()
        != "bearer"
    ):
        raise HTTPException(
            status_code=401,
            detail=(
                "Bearer authentication required"
            ),
            headers={
                "WWW-Authenticate": "Bearer"
            },
        )

    verify_started = perf_counter()
    try:
        verified = verifier.verify(
            credentials.credentials
        )
    except JWTVerificationError:
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid bearer token or identity claims"
            ),
            headers={
                "WWW-Authenticate": "Bearer"
            },
        ) from None
    finally:
        record_api_phase(
            request,
            "auth.jwt_verification",
            (perf_counter() - verify_started) * 1000,
        )

    mapping_started = perf_counter()
    try:
        request_context = mapper.map(
            verified
        )
    except AgentAPIIdentityError:
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid bearer token or identity claims"
            ),
            headers={
                "WWW-Authenticate": "Bearer"
            },
        ) from None
    finally:
        record_api_phase(
            request,
            "auth.request_context_mapping",
            (perf_counter() - mapping_started) * 1000,
        )

    bind_api_request_context(
        request,
        request_context,
    )
    return request_context


def _authorization_blocked(
    result: Any,
) -> bool:
    """判断 Runtime 是否在显式 Authorization 阶段被拒绝。"""

    return any(
        getattr(
            stage,
            "stage",
            "",
        )
        == "authorization"
        and getattr(
            stage,
            "status",
            "",
        )
        == "BLOCKED"
        for stage in (
            getattr(
                result,
                "stage_trace",
                (),
            )
            or ()
        )
    )


def _trace_id(
    result: Any,
) -> str:
    """从统一 Observability Trace 获取公开 trace_id。"""

    observability = getattr(
        result,
        "observability",
        None,
    )
    return str(
        getattr(
            observability,
            "trace_id",
            "",
        )
        or ""
    )


def _audit_guard_event(
    auditor: GovernedAPIGuardAuditor,
    *,
    trace_id: str,
    request_context: RequestContext,
    runtime_status: str,
    duration_ms: float,
) -> None:
    """写入 Admission/Timeout/Backend-Failure Audit。"""

    try:
        auditor.record(
            trace_id=trace_id,
            request_context=(
                request_context
            ),
            runtime_status=(
                runtime_status
            ),
            duration_ms=duration_ms,
        )
    except AuditWriteError:
        if auditor.fail_closed:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": (
                        "AGENT_AUDIT_UNAVAILABLE"
                    ),
                    "trace_id": trace_id,
                },
                headers={
                    "X-Trace-Id": trace_id
                },
            ) from None


async def _safe_release_lease(
    lease,
) -> None:
    """释放 Local/Redis Lease；Redis Release 失败依赖 TTL 保守回收。"""

    try:
        await lease.release_async()
    except TrafficGuardUnavailable:
        # 不能记录 Redis URL/Credential/Error Text。
        # 释放失败会暂时“多占容量”，不会让新请求绕过上限；
        # Expiring Lease 最终自动回收。
        _LOGGER.warning(
            "Agent traffic lease release deferred to TTL recovery."
        )


def _release_when_task_finishes(
    task: asyncio.Task,
    lease,
) -> None:
    """API 504 后，等后台 Worker 真正结束再异步释放共享 Lease。"""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    loop.create_task(
        _safe_release_lease(
            lease
        )
    )


def _run_runtime_with_api_timing(
    runtime,
    question: str,
    request_context: RequestContext,
    scheduled_at: float,
):
    """在线程池执行 Runtime，并拆出 Queue 与 Observer/Audit 的外部耗时。"""

    worker_started = perf_counter()
    queue_wait_ms = max(
        0.0,
        (worker_started - scheduled_at) * 1000,
    )

    result = runtime.run(
        question,
        request_context,
    )
    worker_wall_ms = max(
        0.0,
        (perf_counter() - worker_started) * 1000,
    )

    observability = getattr(
        result,
        "observability",
        None,
    )
    cost = getattr(
        observability,
        "cost",
        None,
    )
    runtime_core_ms = max(
        0.0,
        float(
            getattr(
                cost,
                "total_duration_ms",
                0.0,
            )
            or 0.0
        ),
    )

    # GovernedAgentRuntime 在 Core Timer 结束后才执行 Observer.attach；
    # 因此这里会捕获 Cost Aggregation + Runtime Audit JSONL/fsync 等后置成本。
    post_observer_audit_ms = max(
        0.0,
        worker_wall_ms - runtime_core_ms,
    )
    audit_persistence = getattr(
        observability,
        "audit_persistence",
        None,
    )
    return (
        result,
        queue_wait_ms,
        post_observer_audit_ms,
        audit_persistence,
    )


def _audit_receipt_value(
    receipt: Any,
    field: str,
) -> float:
    """安全读取内部 Audit Receipt 的非负数值字段。"""

    return max(
        0.0,
        float(
            getattr(
                receipt,
                field,
                0.0,
            )
            or 0.0
        ),
    )


def _record_runtime_post_observer_breakdown(
    request: Request,
    *,
    post_observer_total_ms: float,
    audit_receipt: Any,
) -> None:
    """把 Runtime Core 之后的 Observer/Audit 拆成可加和 Phase + 非加和 Metric。

    可加和 Phase：
    - observer_non_audit
    - audit.serialize
    - audit.append_lock_wait
    - audit.append
    - audit.durability_wait
    - audit.writer_residual

    Group Commit 的 Batch Size / Sync Duration 与每请求 Durability Wait 存在重叠，
    因此只作为 Numeric Metric，不加入 Phase Sum，避免 Server Residual 双扣。
    """

    post_total = max(
        0.0,
        float(post_observer_total_ms),
    )

    if audit_receipt is None:
        record_api_phase(
            request,
            "runtime.observer_non_audit",
            post_total,
        )
        return

    audit_total_ms = _audit_receipt_value(
        audit_receipt,
        "total_ms",
    )
    observer_non_audit_ms = max(
        0.0,
        post_total - audit_total_ms,
    )

    record_api_phase(
        request,
        "runtime.observer_non_audit",
        observer_non_audit_ms,
    )

    for phase, field in (
        (
            "runtime.audit.serialize",
            "serialize_ms",
        ),
        (
            "runtime.audit.append_lock_wait",
            "append_lock_wait_ms",
        ),
        (
            "runtime.audit.append",
            "append_ms",
        ),
        (
            "runtime.audit.durability_wait",
            "durability_wait_ms",
        ),
        (
            "runtime.audit.writer_residual",
            "writer_residual_ms",
        ),
    ):
        record_api_phase(
            request,
            phase,
            _audit_receipt_value(
                audit_receipt,
                field,
            ),
        )

    for metric, field in (
        (
            "runtime.audit.batch_id",
            "batch_id",
        ),
        (
            "runtime.audit.batch_total_records",
            "batch_total_records",
        ),
        (
            "runtime.audit.batch_runtime_records",
            "batch_runtime_records",
        ),
        (
            "runtime.audit.batch_api_guard_records",
            "batch_api_guard_records",
        ),
        (
            "runtime.audit.batch_api_timing_records",
            "batch_api_timing_records",
        ),
        (
            "runtime.audit.batch_other_records",
            "batch_other_records",
        ),
        (
            "runtime.audit.batch_coalesce_ms",
            "batch_coalesce_ms",
        ),
        (
            "runtime.audit.batch_sync_ms",
            "batch_sync_ms",
        ),
    ):
        record_api_metric(
            request,
            metric,
            _audit_receipt_value(
                audit_receipt,
                field,
            ),
        )


def create_app() -> FastAPI:
    """创建默认关闭交互式文档的生产 Agent API。"""

    app = FastAPI(
        title=(
            "Commerce Governed Agent API"
        ),
        version="1.2.0",
        description=(
            "Authenticated governed Agent runtime "
            "with local or Redis shared admission control."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # 详细 Phase Timing 默认关闭；SLO Calibration 通过受治理 Env 显式开启。
    # Pure ASGI Middleware 不向 Public Response 注入任何 Timing 字段。
    app.add_middleware(
        GovernedAPITimingMiddleware,
        project_root=ROOT,
    )

    @app.exception_handler(
        AgentAPIConfigurationError
    )
    @app.exception_handler(
        TrafficGuardConfigurationError
    )
    async def configuration_error_handler(
        _request,
        _exc,
    ) -> JSONResponse:
        """服务端安全/Traffic 配置缺失统一投影为 503。"""

        return JSONResponse(
            status_code=503,
            content={
                "detail": (
                    "Agent API is not ready"
                )
            },
        )

    @app.get(
        "/health/live",
        response_model=HealthResponse,
    )
    def live() -> HealthResponse:
        """Liveness：只证明 FastAPI 进程可以响应。"""

        return HealthResponse(
            status="ok"
        )

    @app.get(
        "/health/ready",
        response_model=HealthResponse,
    )
    async def ready() -> HealthResponse:
        """Readiness：Redis 模式必须真实 PING 共享 Traffic Backend。"""

        try:
            get_runtime()
            get_jwt_verifier()
            guard = get_traffic_guard()
            await guard.ready()
            get_guard_auditor()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Agent API is not ready"
                ),
            ) from exc

        return HealthResponse(
            status="ready"
        )

    @app.post(
        "/api/v1/agent/query",
        response_model=AgentQueryResponse,
    )
    async def query_agent(
        payload: AgentQueryRequest,
        response: Response,
        request: Request,
        request_context: RequestContext = Depends(
            get_request_context
        ),
        runtime=Depends(
            get_runtime
        ),
        traffic_guard=Depends(
            get_traffic_guard
        ),
        guard_auditor: GovernedAPIGuardAuditor = Depends(
            get_guard_auditor
        ),
    ) -> AgentQueryResponse:
        """在可信身份、共享 Admission、Concurrency Lease 与 Timeout 下执行 Runtime。"""

        request_started = perf_counter()
        admission_started = perf_counter()

        try:
            lease = await traffic_guard.acquire(
                request_context
            )
            record_api_phase(
                request,
                "admission.shared_guard",
                (perf_counter() - admission_started) * 1000,
            )
        except AdmissionRejected as rejected:
            record_api_phase(
                request,
                "admission.shared_guard",
                (perf_counter() - admission_started) * 1000,
            )
            trace_id = str(
                uuid4()
            )
            elapsed_ms = (
                perf_counter()
                - request_started
            ) * 1000

            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=(
                    request_context
                ),
                runtime_status=(
                    rejected.code
                ),
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "code": rejected.code,
                    "trace_id": trace_id,
                },
                headers={
                    "Retry-After": str(
                        rejected.retry_after_seconds
                    ),
                    "X-Trace-Id": trace_id,
                },
            ) from None
        except TrafficGuardUnavailable:
            record_api_phase(
                request,
                "admission.shared_guard",
                (perf_counter() - admission_started) * 1000,
            )
            trace_id = str(
                uuid4()
            )
            elapsed_ms = (
                perf_counter()
                - request_started
            ) * 1000
            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=(
                    request_context
                ),
                runtime_status=(
                    "TRAFFIC_GUARD_UNAVAILABLE"
                ),
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": (
                        "AGENT_TRAFFIC_GUARD_UNAVAILABLE"
                    ),
                    "trace_id": trace_id,
                },
                headers={
                    "X-Trace-Id": trace_id
                },
            ) from None

        # shield(task) 的关键作用：
        # API 可以先 504，但 Python Worker Thread 不会被伪装成已经终止。
        # Timeout Path 不立即 Release Lease；而是等 Worker 真结束后再释放。
        scheduled_at = perf_counter()
        task = asyncio.create_task(
            run_in_threadpool(
                _run_runtime_with_api_timing,
                runtime,
                payload.question,
                request_context,
                scheduled_at,
            )
        )

        try:
            (
                result,
                queue_wait_ms,
                post_observer_audit_ms,
                audit_persistence,
            ) = await asyncio.wait_for(
                asyncio.shield(
                    task
                ),
                timeout=(
                    traffic_guard.request_timeout_seconds
                ),
            )
            record_api_phase(
                request,
                "threadpool.queue_wait",
                queue_wait_ms,
            )
            _record_runtime_post_observer_breakdown(
                request,
                post_observer_total_ms=(
                    post_observer_audit_ms
                ),
                audit_receipt=(
                    audit_persistence
                ),
            )
        except asyncio.TimeoutError:
            task.add_done_callback(
                lambda done_task: (
                    _release_when_task_finishes(
                        done_task,
                        lease,
                    )
                )
            )

            trace_id = str(
                uuid4()
            )
            elapsed_ms = (
                perf_counter()
                - request_started
            ) * 1000
            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=(
                    request_context
                ),
                runtime_status=(
                    "REQUEST_TIMEOUT"
                ),
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=504,
                detail={
                    "code": (
                        "AGENT_REQUEST_TIMEOUT"
                    ),
                    "trace_id": trace_id,
                },
                headers={
                    "X-Trace-Id": trace_id
                },
            ) from None
        except Exception:
            await _safe_release_lease(
                lease
            )

            trace_id = str(
                uuid4()
            )
            elapsed_ms = (
                perf_counter()
                - request_started
            ) * 1000
            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=(
                    request_context
                ),
                runtime_status=(
                    "RUNTIME_EXCEPTION"
                ),
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": (
                        "AGENT_RUNTIME_ERROR"
                    ),
                    "trace_id": trace_id,
                },
                headers={
                    "X-Trace-Id": trace_id
                },
            ) from None

        # Redis Heartbeat 丢失意味着这次请求已经不能证明自己仍在共享容量预算内。
        # Runtime 已完成也不能把答案当作一次“正常受治理执行”返回。
        if not lease.healthy:
            await _safe_release_lease(
                lease
            )

            trace_id = str(
                uuid4()
            )
            elapsed_ms = (
                perf_counter()
                - request_started
            ) * 1000
            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=(
                    request_context
                ),
                runtime_status=(
                    "TRAFFIC_LEASE_LOST"
                ),
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": (
                        "AGENT_TRAFFIC_LEASE_LOST"
                    ),
                    "trace_id": trace_id,
                },
                headers={
                    "X-Trace-Id": trace_id
                },
            )

        release_started = perf_counter()
        await _safe_release_lease(
            lease
        )
        record_api_phase(
            request,
            "lease.release",
            (perf_counter() - release_started) * 1000,
        )

        trace_id = _trace_id(
            result
        )
        if trace_id:
            response.headers[
                "X-Trace-Id"
            ] = trace_id

        if _authorization_blocked(
            result
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": (
                        "AGENT_AUTHORIZATION_DENIED"
                    ),
                    "trace_id": trace_id,
                },
                headers=(
                    {
                        "X-Trace-Id": (
                            trace_id
                        )
                    }
                    if trace_id
                    else None
                ),
            )

        status = str(
            getattr(
                getattr(
                    result,
                    "status",
                    None,
                ),
                "value",
                getattr(
                    result,
                    "status",
                    "",
                ),
            )
        )
        if status == "ERROR":
            raise HTTPException(
                status_code=500,
                detail={
                    "code": (
                        "AGENT_RUNTIME_ERROR"
                    ),
                    "trace_id": trace_id,
                },
                headers=(
                    {
                        "X-Trace-Id": (
                            trace_id
                        )
                    }
                    if trace_id
                    else None
                ),
            )

        response_build_started = perf_counter()
        public_response = AgentQueryResponse(
            status=status,
            answer=str(
                getattr(
                    result,
                    "answer",
                    "",
                )
                or ""
            ),
            answer_validated=bool(
                getattr(
                    result,
                    "answer_validated",
                    False,
                )
            ),
            trace_id=trace_id,
        )
        record_api_phase(
            request,
            "endpoint.response_build",
            (perf_counter() - response_build_started) * 1000,
        )
        return public_response

    return app


app = create_app()
