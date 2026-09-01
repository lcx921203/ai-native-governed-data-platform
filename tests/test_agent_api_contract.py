"""Production Agent API + Trusted Identity Boundary 契约测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.api.auth import AgentAPIIdentityError, AgentIdentityMapper
from agent.api.main import (
    app,
    get_identity_mapper,
    get_jwt_verifier,
    get_runtime,
)
from agent.runtime import AgentRuntimeStatus, RuntimeStage
from mcp_server.auth.jwt import VerifiedJWT


class FakeVerifier:
    """测试用 JWT Verifier；只返回预构造的已验证身份。"""

    def __init__(self, verified: VerifiedJWT):
        self.verified = verified

    def verify(self, _token: str) -> VerifiedJWT:
        """模拟成功的签名/issuer/audience/exp 验证结果。"""

        return self.verified


class FakeRuntime:
    """记录 API 是否把 RequestContext 与 question 分离传入 Runtime。"""

    def __init__(self, *, authorization_blocked: bool = False):
        self.calls = []
        self.authorization_blocked = authorization_blocked

    def run(self, question, request_context):
        """返回一个最小 Runtime Result，避免测试触达真实 Tool / Provider。"""

        self.calls.append((question, request_context))
        stages = [
            RuntimeStage(
                "authorization",
                "BLOCKED" if self.authorization_blocked else "PASS",
            )
        ]
        status = (
            AgentRuntimeStatus.BLOCKED
            if self.authorization_blocked
            else AgentRuntimeStatus.ANSWERED
        )
        return SimpleNamespace(
            status=status,
            answer="受治理回答",
            answer_validated=True,
            stage_trace=tuple(stages),
            observability=SimpleNamespace(trace_id="trace-test-001"),
        )


def verified_identity(**claim_overrides) -> VerifiedJWT:
    """构造包含显式 tenant/object scope 的测试 VerifiedJWT。"""

    claims = {
        "sub": "user-1",
        "tenant_id": "tenant-west",
        "roles": ["analyst"],
        "allowed_metrics": ["gross_sales", "order_count"],
        "allowed_datasets": ["orders"],
        "allowed_entities": ["*"],
        "allowed_dimensions": ["store__region"],
        "allowed_knowledge_scopes": ["architecture"],
        "dimension_scopes": {
            "store__region": "West",
        },
    }
    claims.update(claim_overrides)
    return VerifiedJWT(
        subject="user-1",
        client_id="client-1",
        scopes=("commerce:semantic:read",),
        expires_at=1893456000,
        claims=claims,
    )


@pytest.fixture(autouse=True)
def clean_dependency_overrides():
    """每个 API 测试后清理 FastAPI Dependency Override。"""

    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_identity_mapper_builds_fail_closed_request_context_without_token():
    """Verified claims 应映射成 RequestContext，且不携带 Bearer Token。"""

    context = AgentIdentityMapper(".").map(verified_identity())

    assert context.tenant_id == "tenant-west"
    assert context.subject == "user-1"
    assert context.allowed_metrics == frozenset(
        {"gross_sales", "order_count"}
    )
    assert context.dimension_scopes[0].dimension == "store__region"
    assert context.dimension_scopes[0].values == ("West",)

    serialized = context.to_dict()
    assert "token" not in serialized
    assert "bearer" not in str(serialized).lower()


def test_identity_mapper_missing_object_claim_defaults_to_deny():
    """对象 allowlist 缺失不能默认扩权为 '*'。"""

    verified = verified_identity()
    verified.claims.pop("allowed_metrics")

    context = AgentIdentityMapper(".").map(verified)

    assert context.allowed_metrics == frozenset()


def test_identity_mapper_requires_tenant_claim():
    """已验证 JWT 缺 tenant_id 时仍必须拒绝形成 RequestContext。"""

    verified = verified_identity()
    verified.claims.pop("tenant_id")

    with pytest.raises(AgentAPIIdentityError):
        AgentIdentityMapper(".").map(verified)


def test_agent_query_requires_bearer_authentication():
    """Agent Query 没有 Bearer Header 时返回 401。"""

    app.dependency_overrides[get_jwt_verifier] = lambda: FakeVerifier(
        verified_identity()
    )
    app.dependency_overrides[get_runtime] = lambda: FakeRuntime()

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/query",
            json={"question": "gross_sales 的定义是什么？"},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_agent_query_passes_verified_request_context_to_runtime():
    """API 必须把 JWT 映射后的 RequestContext 作为独立参数传给 Runtime。"""

    fake_runtime = FakeRuntime()
    app.dependency_overrides[get_jwt_verifier] = lambda: FakeVerifier(
        verified_identity()
    )
    app.dependency_overrides[get_runtime] = lambda: fake_runtime
    app.dependency_overrides[get_identity_mapper] = lambda: AgentIdentityMapper(
        "."
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/query",
            headers={"Authorization": "Bearer opaque-test-token"},
            json={"question": "gross_sales 的定义是什么？"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ANSWERED",
        "answer": "受治理回答",
        "answer_validated": True,
        "trace_id": "trace-test-001",
    }
    assert response.headers["x-trace-id"] == "trace-test-001"

    assert len(fake_runtime.calls) == 1
    question, context = fake_runtime.calls[0]
    assert question == "gross_sales 的定义是什么？"
    assert context.tenant_id == "tenant-west"
    assert context.scopes == frozenset({"commerce:semantic:read"})


def test_authenticated_but_runtime_authorization_denied_returns_403():
    """JWT 有效但 Agent capability/object scope 不足时投影为 403。"""

    app.dependency_overrides[get_jwt_verifier] = lambda: FakeVerifier(
        verified_identity()
    )
    app.dependency_overrides[get_runtime] = lambda: FakeRuntime(
        authorization_blocked=True
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/query",
            headers={"Authorization": "Bearer opaque-test-token"},
            json={"question": "gross_sales 的定义是什么？"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == (
        "AGENT_AUTHORIZATION_DENIED"
    )
    assert response.json()["detail"]["trace_id"] == "trace-test-001"


def test_public_agent_response_does_not_expose_internal_runtime_objects():
    """公共 Response Contract 不应返回 Router / Context / Tool Trace。"""

    fake_runtime = FakeRuntime()
    app.dependency_overrides[get_jwt_verifier] = lambda: FakeVerifier(
        verified_identity()
    )
    app.dependency_overrides[get_runtime] = lambda: fake_runtime

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent/query",
            headers={"Authorization": "Bearer opaque-test-token"},
            json={"question": "gross_sales 的定义是什么？"},
        )

    payload = response.json()
    assert "route" not in payload
    assert "context_plan" not in payload
    assert "tool_trace" not in payload
    assert "warnings" not in payload


def test_agent_api_interactive_docs_are_disabled():
    """生产 Agent API 默认不暴露 Swagger / ReDoc / OpenAPI 文档端点。"""

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
