"""Agent API Local Rate Limit / Concurrency / Timeout Guard 契约测试。"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent.api.guard_audit import (
    GovernedAPIGuardAuditor,
)
from agent.api.main import (
    app,
    get_guard_auditor,
    get_jwt_verifier,
    get_runtime,
    get_traffic_guard,
)
from agent.api.traffic import (
    AdmissionRejected,
    GovernedTrafficGuard,
)
from agent.runtime import (
    AgentRuntimeStatus,
    RuntimeStage,
)
from agent.tenancy import RequestContext
from mcp_server.auth.jwt import VerifiedJWT


ROOT = "."


def _context(
    tenant_id: str = "tenant-west",
    subject: str = "user-1",
) -> RequestContext:
    """构造受信任测试身份。"""

    return RequestContext(
        tenant_id=tenant_id,
        subject=subject,
        scopes=frozenset(
            {"commerce:semantic:read"}
        ),
        allowed_metrics=frozenset(
            {"gross_sales"}
        ),
    )


def _guard(
    monkeypatch,
    **overrides,
) -> GovernedTrafficGuard:
    """用环境变量构造隔离的 Local Traffic Guard。"""

    values = {
        "AGENT_API_REQUEST_TIMEOUT_SECONDS": "1",
        "AGENT_API_GLOBAL_CONCURRENCY": "10",
        "AGENT_API_TENANT_CONCURRENCY": "5",
        "AGENT_API_SUBJECT_RPM": "30",
        "AGENT_API_TENANT_RPM": "120",
        "AGENT_API_MAX_TRACKED_IDENTITIES": "1000",
    }
    values.update(
        {
            key: str(value)
            for key, value
            in overrides.items()
        }
    )
    for key, value in values.items():
        monkeypatch.setenv(
            key,
            value,
        )
    return GovernedTrafficGuard(
        ROOT
    )


def test_subject_rate_limit_is_keyed_by_verified_identity(
    monkeypatch,
):
    """同一 Tenant + Subject 超过 RPM 后必须 429 Admission。"""

    guard = _guard(
        monkeypatch,
        AGENT_API_SUBJECT_RPM=1,
    )

    async def scenario():
        first = await guard.acquire(
            _context()
        )
        first.release()

        try:
            await guard.acquire(
                _context()
            )
        except AdmissionRejected as rejected:
            return rejected

        raise AssertionError(
            "second request should be rejected"
        )

    rejected = asyncio.run(
        scenario()
    )
    assert (
        rejected.code
        == "SUBJECT_RATE_LIMITED"
    )
    assert (
        rejected.retry_after_seconds
        >= 1
    )


def test_tenant_concurrency_rejects_without_queueing(
    monkeypatch,
):
    """Tenant 并发已满时立即拒绝，不进入无界等待队列。"""

    guard = _guard(
        monkeypatch,
        AGENT_API_TENANT_CONCURRENCY=1,
    )

    async def scenario():
        first = await guard.acquire(
            _context()
        )
        try:
            try:
                await guard.acquire(
                    _context(
                        subject="user-2"
                    )
                )
            except AdmissionRejected as rejected:
                assert (
                    rejected.code
                    == "TENANT_CONCURRENCY_LIMIT"
                )
            else:
                raise AssertionError(
                    "concurrent request should be rejected"
                )
        finally:
            first.release()

        third = await guard.acquire(
            _context(
                subject="user-3"
            )
        )
        third.release()

    asyncio.run(
        scenario()
    )
    assert (
        guard.snapshot()[
            "global_active"
        ]
        == 0
    )


class _FakeVerifier:
    """API Test 用 VerifiedJWT Provider。"""

    def verify(
        self,
        _token: str,
    ) -> VerifiedJWT:
        """返回一个显式 tenant/object scope 的已验证身份。"""

        return VerifiedJWT(
            subject="user-1",
            client_id="client-1",
            scopes=(
                "commerce:semantic:read",
            ),
            expires_at=1893456000,
            claims={
                "sub": "user-1",
                "tenant_id": "tenant-west",
                "allowed_metrics": [
                    "gross_sales"
                ],
                "allowed_datasets": [],
                "allowed_entities": [],
                "allowed_dimensions": [],
                "allowed_knowledge_scopes": [],
                "dimension_scopes": {},
            },
        )


class _FastRuntime:
    """立即返回的测试 Runtime。"""

    def run(
        self,
        _question,
        _request_context,
    ):
        """返回一个最小已验证结果。"""

        return SimpleNamespace(
            status=(
                AgentRuntimeStatus.ANSWERED
            ),
            answer="ok",
            answer_validated=True,
            stage_trace=(
                RuntimeStage(
                    "authorization",
                    "PASS",
                ),
            ),
            observability=(
                SimpleNamespace(
                    trace_id="runtime-trace"
                )
            ),
        )


class _SlowRuntime:
    """用于验证 API Timeout 不等价于 Worker 已终止。"""

    def run(
        self,
        _question,
        _request_context,
    ):
        """短暂睡眠后返回；后台 Worker 真结束前不能释放 Lease。"""

        time.sleep(
            0.15
        )
        return SimpleNamespace(
            status=(
                AgentRuntimeStatus.ANSWERED
            ),
            answer="late",
            answer_validated=True,
            stage_trace=(
                RuntimeStage(
                    "authorization",
                    "PASS",
                ),
            ),
            observability=(
                SimpleNamespace(
                    trace_id=(
                        "late-runtime-trace"
                    )
                )
            ),
        )


def test_http_rate_limit_returns_429_and_trace_header(
    monkeypatch,
):
    """公共 API 的 Admission Rejection 必须返回 429 + Retry-After + trace_id。"""

    guard = _guard(
        monkeypatch,
        AGENT_API_SUBJECT_RPM=1,
    )
    app.dependency_overrides[
        get_jwt_verifier
    ] = lambda: _FakeVerifier()
    app.dependency_overrides[
        get_runtime
    ] = lambda: _FastRuntime()
    app.dependency_overrides[
        get_traffic_guard
    ] = lambda: guard
    app.dependency_overrides[
        get_guard_auditor
    ] = lambda: GovernedAPIGuardAuditor(
        ROOT
    )

    try:
        with TestClient(
            app
        ) as client:
            first = client.post(
                "/api/v1/agent/query",
                headers={
                    "Authorization": (
                        "Bearer opaque"
                    )
                },
                json={
                    "question": (
                        "gross_sales 是多少？"
                    )
                },
            )
            second = client.post(
                "/api/v1/agent/query",
                headers={
                    "Authorization": (
                        "Bearer opaque"
                    )
                },
                json={
                    "question": (
                        "gross_sales 是多少？"
                    )
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 429
    assert (
        second.json()[
            "detail"
        ]["code"]
        == "SUBJECT_RATE_LIMITED"
    )
    assert second.headers[
        "retry-after"
    ]
    assert (
        second.headers[
            "x-trace-id"
        ]
        == second.json()[
            "detail"
        ]["trace_id"]
    )


def test_timeout_returns_504_but_keeps_capacity_until_worker_finishes(
    monkeypatch,
):
    """API Timeout 先返回 504，但 Lease 必须等 Worker 真结束后才释放。"""

    guard = _guard(
        monkeypatch,
        AGENT_API_REQUEST_TIMEOUT_SECONDS=0.05,
        AGENT_API_TENANT_CONCURRENCY=1,
    )
    app.dependency_overrides[
        get_jwt_verifier
    ] = lambda: _FakeVerifier()
    app.dependency_overrides[
        get_runtime
    ] = lambda: _SlowRuntime()
    app.dependency_overrides[
        get_traffic_guard
    ] = lambda: guard
    app.dependency_overrides[
        get_guard_auditor
    ] = lambda: GovernedAPIGuardAuditor(
        ROOT
    )

    try:
        with TestClient(
            app
        ) as client:
            timed_out = client.post(
                "/api/v1/agent/query",
                headers={
                    "Authorization": (
                        "Bearer opaque"
                    )
                },
                json={
                    "question": (
                        "gross_sales 是多少？"
                    )
                },
            )
            assert (
                timed_out.status_code
                == 504
            )
            assert (
                timed_out.json()[
                    "detail"
                ]["code"]
                == "AGENT_REQUEST_TIMEOUT"
            )

            # Worker 仍在后台执行，所以此时 Local Tenant/Global Slot 仍被占用。
            immediately_after = (
                guard.snapshot()
            )
            assert (
                immediately_after[
                    "global_active"
                ]
                == 1
            )

            time.sleep(
                0.20
            )
            assert (
                guard.snapshot()[
                    "global_active"
                ]
                == 0
            )
    finally:
        app.dependency_overrides.clear()


def test_guard_event_can_be_queried_from_same_audit_store(
    monkeypatch,
    tmp_path,
):
    """429/504 Guard Event 使用同一 JSONL Audit Store，但不保存 Prompt。"""

    path = (
        tmp_path
        / "audit.jsonl"
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_MODE",
        "jsonl",
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_PATH",
        str(path),
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_FAILURE_MODE",
        "fail_closed",
    )

    auditor = (
        GovernedAPIGuardAuditor(
            ROOT
        )
    )
    auditor.record(
        trace_id="guard-trace-1",
        request_context=_context(),
        runtime_status=(
            "SUBJECT_RATE_LIMITED"
        ),
        duration_ms=2.5,
    )

    row = json.loads(
        path.read_text(
            encoding="utf-8"
        ).strip()
    )
    assert (
        row["event_type"]
        == "API_GUARD"
    )
    assert (
        row["runtime_status"]
        == "SUBJECT_RATE_LIMITED"
    )
    assert (
        row["trace_id"]
        == "guard-trace-1"
    )
    assert "question" not in row
    assert "answer" not in row
