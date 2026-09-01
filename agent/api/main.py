"""Production Agent API + Trusted Identity + SLO Guard（生产入口保护）。

HTTP 链路：
    Bearer JWT
      -> Verified RequestContext
      -> Subject/Tenant Rate Limit
      -> Tenant/Global Concurrency Admission
      -> GovernedAgentRuntime
      -> Timeout Boundary
      -> Answer Validator
      -> minimal public response

V1 Traffic Guard 是 process-local；多 Worker / 多 Pod 的全局限流需要共享基础设施。
"""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import yaml
from fastapi import Depends, FastAPI, HTTPException, Response, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from agent.audit import AuditWriteError
from agent.runtime import build_runtime_from_env
from agent.tenancy import RequestContext
from mcp_server.auth.jwt import (
    JWKSJWTVerifier,
    JWTVerificationError,
)

from .auth import AgentAPIIdentityError, AgentIdentityMapper
from .contracts import AgentQueryRequest, AgentQueryResponse, HealthResponse
from .guard_audit import GovernedAPIGuardAuditor
from .traffic import (
    AdmissionRejected,
    GovernedTrafficGuard,
    TrafficGuardConfigurationError,
)


ROOT = Path(__file__).resolve().parents[2]
_BEARER = HTTPBearer(auto_error=False)


class AgentAPIConfigurationError(RuntimeError):
    """Agent API 的认证或运行配置不完整。"""


@lru_cache(maxsize=1)
def get_api_policy() -> dict[str, Any]:
    """读取 Agent API 的仓库治理策略。"""

    return yaml.safe_load(
        (ROOT / "agent/contracts/agent_api_policy.yml").read_text(
            encoding="utf-8"
        )
    )


@lru_cache(maxsize=1)
def get_runtime():
    """构造进程级 Single Agent Runtime；Renderer 模式仍由现有 Factory 控制。"""

    return build_runtime_from_env(ROOT)


@lru_cache(maxsize=1)
def get_identity_mapper() -> AgentIdentityMapper:
    """构造进程级 JWT claims -> RequestContext Mapper。"""

    return AgentIdentityMapper(ROOT)


@lru_cache(maxsize=1)
def get_traffic_guard() -> GovernedTrafficGuard:
    """构造进程级 Admission Guard；它不声称是集群共享限流器。"""

    return GovernedTrafficGuard(ROOT)


@lru_cache(maxsize=1)
def get_guard_auditor() -> GovernedAPIGuardAuditor:
    """构造 API Guard Event 审计器。"""

    return GovernedAPIGuardAuditor(ROOT)


@lru_cache(maxsize=1)
def get_jwt_verifier() -> JWKSJWTVerifier:
    """读取 Agent API 专属 JWKS 配置并复用现有 JWT 验证实现。"""

    config = get_api_policy()["authentication"]["jwt"]
    jwks_url = os.getenv(str(config["jwks_url_env"]), "").strip()
    issuer = os.getenv(str(config["issuer_env"]), "").strip()
    audience = os.getenv(str(config["audience_env"]), "").strip()

    if not jwks_url or not issuer or not audience:
        raise AgentAPIConfigurationError(
            "Agent API JWT verifier is not configured."
        )

    return JWKSJWTVerifier(
        jwks_url=jwks_url,
        issuer=issuer,
        audience=audience,
        algorithms=tuple(str(x) for x in config["algorithms"]),
    )


def get_request_context(
    credentials: HTTPAuthorizationCredentials | None = Security(_BEARER),
    verifier: JWKSJWTVerifier = Depends(get_jwt_verifier),
    mapper: AgentIdentityMapper = Depends(get_identity_mapper),
) -> RequestContext:
    """验证 Bearer JWT，并只把最小受控身份信息下传给 Agent。"""

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        verified = verifier.verify(credentials.credentials)
        return mapper.map(verified)
    except (JWTVerificationError, AgentAPIIdentityError):
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token or identity claims",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def _authorization_blocked(result: Any) -> bool:
    """判断 Runtime 是否在显式 Authorization 阶段被拒绝。"""

    return any(
        getattr(stage, "stage", "") == "authorization"
        and getattr(stage, "status", "") == "BLOCKED"
        for stage in getattr(result, "stage_trace", ()) or ()
    )


def _trace_id(result: Any) -> str:
    """从统一 Observability Trace 获取公开 trace_id。"""

    observability = getattr(result, "observability", None)
    return str(getattr(observability, "trace_id", "") or "")


def _audit_guard_event(
    auditor: GovernedAPIGuardAuditor,
    *,
    trace_id: str,
    request_context: RequestContext,
    runtime_status: str,
    duration_ms: float,
) -> None:
    """写入 Admission/Timeout Audit；Fail-Closed 模式下写失败投影为 503。"""

    try:
        auditor.record(
            trace_id=trace_id,
            request_context=request_context,
            runtime_status=runtime_status,
            duration_ms=duration_ms,
        )
    except AuditWriteError:
        if auditor.fail_closed:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "AGENT_AUDIT_UNAVAILABLE",
                    "trace_id": trace_id,
                },
                headers={"X-Trace-Id": trace_id},
            ) from None


def create_app() -> FastAPI:
    """创建默认关闭交互式文档的生产 Agent API。"""

    app = FastAPI(
        title="Commerce Governed Agent API",
        version="1.1.0",
        description="Authenticated governed Agent runtime with process-local admission control.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(
        (AgentAPIConfigurationError, TrafficGuardConfigurationError)
    )
    async def configuration_error_handler(
        _request,
        _exc,
    ) -> JSONResponse:
        """服务端安全/流量配置缺失统一投影为 503。"""

        return JSONResponse(
            status_code=503,
            content={"detail": "Agent API is not ready"},
        )

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        """Liveness：只证明 FastAPI 进程可以响应。"""

        return HealthResponse(status="ok")

    @app.get("/health/ready", response_model=HealthResponse)
    def ready() -> HealthResponse:
        """Readiness：确认 Runtime、认证和 Traffic Guard 都可构造。"""

        try:
            get_runtime()
            get_jwt_verifier()
            get_traffic_guard()
            get_guard_auditor()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Agent API is not ready",
            ) from exc
        return HealthResponse(status="ready")

    @app.post(
        "/api/v1/agent/query",
        response_model=AgentQueryResponse,
    )
    async def query_agent(
        payload: AgentQueryRequest,
        response: Response,
        request_context: RequestContext = Depends(get_request_context),
        runtime=Depends(get_runtime),
        traffic_guard: GovernedTrafficGuard = Depends(get_traffic_guard),
        guard_auditor: GovernedAPIGuardAuditor = Depends(get_guard_auditor),
    ) -> AgentQueryResponse:
        """在可信身份、限流、并发与 Timeout Guard 下执行 Agent Runtime。"""

        request_started = perf_counter()
        try:
            lease = await traffic_guard.acquire(request_context)
        except AdmissionRejected as rejected:
            trace_id = str(uuid4())
            elapsed_ms = (perf_counter() - request_started) * 1000
            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=request_context,
                runtime_status=rejected.code,
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "code": rejected.code,
                    "trace_id": trace_id,
                },
                headers={
                    "Retry-After": str(rejected.retry_after_seconds),
                    "X-Trace-Id": trace_id,
                },
            ) from None

        # shield(task) 的关键作用：
        # API 超时可以先返回 504，但 Python Worker Thread 不会被伪装成已经终止。
        # lease 只在 Task 真正结束的 callback 中释放，因此超时请求仍占容量。
        task = asyncio.create_task(
            run_in_threadpool(
                runtime.run,
                payload.question,
                request_context,
            )
        )
        task.add_done_callback(lambda _task: lease.release())

        try:
            result = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=traffic_guard.request_timeout_seconds,
            )
        except asyncio.TimeoutError:
            trace_id = str(uuid4())
            elapsed_ms = (perf_counter() - request_started) * 1000
            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=request_context,
                runtime_status="REQUEST_TIMEOUT",
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "AGENT_REQUEST_TIMEOUT",
                    "trace_id": trace_id,
                },
                headers={"X-Trace-Id": trace_id},
            ) from None
        except Exception:
            trace_id = str(uuid4())
            elapsed_ms = (perf_counter() - request_started) * 1000
            _audit_guard_event(
                guard_auditor,
                trace_id=trace_id,
                request_context=request_context,
                runtime_status="RUNTIME_EXCEPTION",
                duration_ms=elapsed_ms,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "AGENT_RUNTIME_ERROR",
                    "trace_id": trace_id,
                },
                headers={"X-Trace-Id": trace_id},
            ) from None

        trace_id = _trace_id(result)
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id

        if _authorization_blocked(result):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "AGENT_AUTHORIZATION_DENIED",
                    "trace_id": trace_id,
                },
                headers={"X-Trace-Id": trace_id} if trace_id else None,
            )

        status = str(
            getattr(
                getattr(result, "status", None),
                "value",
                getattr(result, "status", ""),
            )
        )
        if status == "ERROR":
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "AGENT_RUNTIME_ERROR",
                    "trace_id": trace_id,
                },
                headers={"X-Trace-Id": trace_id} if trace_id else None,
            )

        return AgentQueryResponse(
            status=status,
            answer=str(getattr(result, "answer", "") or ""),
            answer_validated=bool(
                getattr(result, "answer_validated", False)
            ),
            trace_id=trace_id,
        )

    return app


app = create_app()
