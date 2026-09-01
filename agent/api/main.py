"""Production Agent API + Trusted Identity Boundary（可信身份边界）。

HTTP 链路：
    Bearer JWT
      -> JWKS signature / issuer / audience / exp verification
      -> VerifiedJWT
      -> AgentIdentityMapper
      -> RequestContext
      -> GovernedAgentRuntime
      -> Answer Validator
      -> minimal public response

工程边界：
- API 不从 Prompt 推断 tenant / role / scope；
- 原始 Bearer Token 不进入 Agent Runtime；
- 401 只用于认证失败，403 用于已认证但授权不足；
- Router / Tool Trace / 内部 Warning 不作为公共 API 返回；
- Live LLM Renderer 是否启用仍由已有 Runtime Factory 的显式环境门控制。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from fastapi import Depends, FastAPI, HTTPException, Response, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from agent.runtime import build_runtime_from_env
from agent.tenancy import RequestContext
from mcp_server.auth.jwt import (
    JWKSJWTVerifier,
    JWTVerificationError,
)

from .auth import AgentAPIIdentityError, AgentIdentityMapper
from .contracts import AgentQueryRequest, AgentQueryResponse, HealthResponse


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
        # 不能把 JWT 解析细节、issuer/audience 配置或 claim 内容回显给调用方。
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


def create_app() -> FastAPI:
    """创建默认关闭交互式文档的生产 Agent API。"""

    app = FastAPI(
        title="Commerce Governed Agent API",
        version="1.0.0",
        description="Authenticated governed Agent runtime over trusted RequestContext.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(AgentAPIConfigurationError)
    async def configuration_error_handler(
        _request,
        _exc: AgentAPIConfigurationError,
    ) -> JSONResponse:
        """把服务端配置缺失统一投影为 503，避免泄露认证配置细节。"""

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
        """Readiness：确认 Runtime 与 JWT verifier 至少可以完成本地配置构造。"""

        try:
            get_runtime()
            get_jwt_verifier()
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
    ) -> AgentQueryResponse:
        """在可信 RequestContext 下执行一次统一 Agent Runtime。"""

        result = await run_in_threadpool(
            runtime.run,
            payload.question,
            request_context,
        )

        trace_id = _trace_id(result)
        if trace_id:
            response.headers["X-Trace-Id"] = trace_id

        # 认证已经成功，但 capability/object/tenant scope 不允许。
        if _authorization_blocked(result):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "AGENT_AUTHORIZATION_DENIED",
                    "trace_id": trace_id,
                },
            )

        status = str(
            getattr(
                getattr(result, "status", None),
                "value",
                getattr(result, "status", ""),
            )
        )
        if status == "ERROR":
            # 内部 Provider/Tool/Validator 错误只返回 trace_id，详细原因留在受治理 Trace。
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "AGENT_RUNTIME_ERROR",
                    "trace_id": trace_id,
                },
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
