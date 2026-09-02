"""Real Redis Runtime Acceptance（真实 Redis 运行验收）。

普通 ``pytest -q`` 不要求本机 Redis，因此默认跳过本文件。
GitHub Actions 的 ``redis-runtime-acceptance`` Job 会：
1. 启动真实 Redis Service；
2. 从 agent-redis 临时 hash lock 安装 redis-py；
3. 设置 ``AGENT_REDIS_RUNTIME_ACCEPTANCE=true``；
4. 运行本文件并上传 JUnit XML 作为 Runtime Evidence。

这里不使用 Fake Client，重点验证跨连接共享状态、真实 Lua EVAL 和 Lease TTL 回收。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

if (
    os.getenv(
        "AGENT_REDIS_RUNTIME_ACCEPTANCE",
        "",
    ).strip().lower()
    != "true"
):
    pytest.skip(
        "Real Redis runtime acceptance is only enabled in its dedicated CI job.",
        allow_module_level=True,
    )

redis_async = pytest.importorskip(
    "redis.asyncio"
)

from agent.api.redis_traffic import (  # noqa: E402
    RedisSharedTrafficBackend,
    RedisTrafficGuard,
)
from agent.api.traffic import (  # noqa: E402
    AdmissionRejected,
    build_traffic_guard_from_env,
    load_traffic_limits,
)
from agent.tenancy import RequestContext  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
REDIS_URL = os.environ[
    "AGENT_API_REDIS_URL"
]


def _context(
    *,
    tenant_id: str = "tenant-west",
    subject: str = "user-1",
) -> RequestContext:
    """构造真实 Redis Admission 使用的可信身份。"""

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


def _namespace(
    label: str,
) -> str:
    """每个测试使用唯一 Namespace，避免并发 CI / 重跑互相污染。"""

    base = os.getenv(
        "AGENT_API_REDIS_NAMESPACE",
        "commerce:agent:acceptance",
    ).strip()
    return (
        f"{base}:{label}:"
        f"{uuid4().hex}"
    )


def _configure_limits(
    monkeypatch,
    *,
    namespace: str,
    subject_rpm: int = 100,
    tenant_rpm: int = 100,
    tenant_concurrency: int = 1,
    global_concurrency: int = 2,
) -> None:
    """设置一组测试级 Guardrail；所有值只作用于当前 pytest 进程。"""

    monkeypatch.setenv(
        "AGENT_API_TRAFFIC_BACKEND",
        "redis",
    )
    monkeypatch.setenv(
        "AGENT_API_REDIS_URL",
        REDIS_URL,
    )
    monkeypatch.setenv(
        "AGENT_API_REDIS_NAMESPACE",
        namespace,
    )
    monkeypatch.setenv(
        "AGENT_API_REDIS_OPERATION_TIMEOUT_SECONDS",
        "1",
    )
    monkeypatch.setenv(
        "AGENT_API_REDIS_LEASE_TTL_SECONDS",
        "10",
    )
    monkeypatch.setenv(
        "AGENT_API_REDIS_HEARTBEAT_SECONDS",
        "2",
    )

    monkeypatch.setenv(
        "AGENT_API_SUBJECT_RPM",
        str(subject_rpm),
    )
    monkeypatch.setenv(
        "AGENT_API_TENANT_RPM",
        str(tenant_rpm),
    )
    monkeypatch.setenv(
        "AGENT_API_TENANT_CONCURRENCY",
        str(tenant_concurrency),
    )
    monkeypatch.setenv(
        "AGENT_API_GLOBAL_CONCURRENCY",
        str(global_concurrency),
    )


async def _close_guard(
    guard,
) -> None:
    """关闭测试 Guard 的 Redis Connection Pool，避免 CI Resource Warning。"""

    await guard.backend.client.aclose()


def test_real_redis_factory_and_readiness(
    monkeypatch,
):
    """Factory 必须真实构造 Redis Backend，并通过 Redis PING Readiness。"""

    _configure_limits(
        monkeypatch,
        namespace=_namespace(
            "ready"
        ),
    )

    async def scenario():
        guard = (
            build_traffic_guard_from_env(
                ROOT
            )
        )
        try:
            assert (
                guard.backend_name
                == "redis"
            )
            assert (
                await guard.ready()
                is True
            )
        finally:
            await _close_guard(
                guard
            )

    asyncio.run(
        scenario()
    )


def test_real_redis_shares_tenant_concurrency_across_connections(
    monkeypatch,
):
    """两个独立 Redis Client / Guard 必须竞争同一 Tenant Concurrency。"""

    _configure_limits(
        monkeypatch,
        namespace=_namespace(
            "concurrency"
        ),
        tenant_concurrency=1,
        global_concurrency=2,
    )

    async def scenario():
        first_guard = (
            RedisTrafficGuard.from_env(
                ROOT
            )
        )
        second_guard = (
            RedisTrafficGuard.from_env(
                ROOT
            )
        )

        first_lease = None
        second_lease = None
        try:
            first_lease = await first_guard.acquire(
                _context(
                    subject="user-1"
                )
            )

            with pytest.raises(
                AdmissionRejected
            ) as captured:
                await second_guard.acquire(
                    _context(
                        subject="user-2"
                    )
                )

            assert (
                captured.value.code
                == "TENANT_CONCURRENCY_LIMIT"
            )

            await first_lease.release_async()
            first_lease = None

            second_lease = await second_guard.acquire(
                _context(
                    subject="user-2"
                )
            )
        finally:
            if first_lease is not None:
                await first_lease.release_async()
            if second_lease is not None:
                await second_lease.release_async()

            await _close_guard(
                first_guard
            )
            await _close_guard(
                second_guard
            )

    asyncio.run(
        scenario()
    )


def test_real_redis_shares_subject_rate_window_across_connections(
    monkeypatch,
):
    """两个独立 Guard 的顺序请求必须累计在同一个 Subject Sliding Window。"""

    _configure_limits(
        monkeypatch,
        namespace=_namespace(
            "rate"
        ),
        subject_rpm=2,
        tenant_rpm=100,
        tenant_concurrency=5,
        global_concurrency=10,
    )

    async def scenario():
        first_guard = (
            RedisTrafficGuard.from_env(
                ROOT
            )
        )
        second_guard = (
            RedisTrafficGuard.from_env(
                ROOT
            )
        )
        try:
            lease = await first_guard.acquire(
                _context()
            )
            await lease.release_async()

            lease = await second_guard.acquire(
                _context()
            )
            await lease.release_async()

            with pytest.raises(
                AdmissionRejected
            ) as captured:
                await first_guard.acquire(
                    _context()
                )

            assert (
                captured.value.code
                == "SUBJECT_RATE_LIMITED"
            )
            assert (
                captured.value.retry_after_seconds
                >= 1
            )
        finally:
            await _close_guard(
                first_guard
            )
            await _close_guard(
                second_guard
            )

    asyncio.run(
        scenario()
    )


def test_real_redis_reclaims_orphaned_concurrency_lease_after_ttl(
    monkeypatch,
):
    """模拟 Pod 崩溃不执行 Release：Lease TTL 到期后必须恢复共享容量。"""

    _configure_limits(
        monkeypatch,
        namespace=_namespace(
            "orphan"
        ),
        subject_rpm=100,
        tenant_rpm=100,
        tenant_concurrency=1,
        global_concurrency=1,
    )

    async def scenario():
        client = redis_async.from_url(
            REDIS_URL,
            decode_responses=True,
        )
        backend = RedisSharedTrafficBackend(
            client,
            namespace=os.environ[
                "AGENT_API_REDIS_NAMESPACE"
            ],
            hash_tag=(
                "governed-agent-traffic"
            ),
            operation_timeout_seconds=1,
        )
        limits = load_traffic_limits(
            ROOT
        )

        try:
            first = await backend.admit(
                tenant_id="tenant-west",
                subject="user-1",
                limits=limits,
                lease_ttl_ms=400,
                lease_id="orphan-lease",
            )
            assert first.admitted is True

            blocked = await backend.admit(
                tenant_id="tenant-west",
                subject="user-2",
                limits=limits,
                lease_ttl_ms=400,
                lease_id="blocked-before-ttl",
            )
            assert (
                blocked.admitted
                is False
            )
            assert (
                blocked.code
                == "TENANT_CONCURRENCY_LIMIT"
            )

            # 不调用 release，模拟 Worker/Pod 直接消失。
            await asyncio.sleep(
                0.70
            )

            recovered = await backend.admit(
                tenant_id="tenant-west",
                subject="user-3",
                limits=limits,
                lease_ttl_ms=400,
                lease_id="after-ttl",
            )
            assert (
                recovered.admitted
                is True
            )

            await backend.release(
                tenant_id="tenant-west",
                lease_id="after-ttl",
            )
        finally:
            await client.aclose()

    asyncio.run(
        scenario()
    )
