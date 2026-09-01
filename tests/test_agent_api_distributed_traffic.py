"""Redis Shared Traffic Guard 的原子性、隐私与 Fail-Closed 契约测试。

测试不要求 CI 启动真实 Redis：
- Redis Lua 的调用边界由 Fake Client 验证；
- 跨 Guard 共享语义由 Fake Shared Backend 验证；
- 真实 Redis Runtime Verification 留给部署/验收环境。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

import pytest

from agent.api.redis_traffic import (
    ADMISSION_LUA,
    RELEASE_LUA,
    RENEW_LUA,
    RedisSharedTrafficBackend,
    RedisTrafficGuard,
    SharedAdmissionDecision,
)
from agent.api.traffic import (
    AdmissionRejected,
    TrafficGuardConfigurationError,
    TrafficGuardUnavailable,
    build_traffic_guard_from_env,
    load_traffic_limits,
)
from agent.tenancy import RequestContext


ROOT = Path(__file__).resolve().parents[1]


def _context(
    tenant_id: str = "tenant-west",
    subject: str = "user-1",
) -> RequestContext:
    """构造共享 Traffic Guard 使用的可信身份。"""

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


class _EvalClient:
    """捕获 Redis EVAL 调用，不需要真实 Redis Server。"""

    def __init__(
        self,
        *,
        response=None,
        fail: bool = False,
    ):
        self.response = (
            [1, 0, 0]
            if response is None
            else response
        )
        self.fail = fail
        self.calls = []

    async def ping(self):
        """模拟 Redis PING。"""

        if self.fail:
            raise RuntimeError(
                "redis down"
            )
        return True

    async def eval(
        self,
        script,
        numkeys,
        *parts,
    ):
        """记录 Script/KEYS/ARGV 并返回预设结果。"""

        if self.fail:
            raise RuntimeError(
                "redis down"
            )
        self.calls.append(
            (
                script,
                numkeys,
                parts,
            )
        )
        return self.response


def test_redis_admission_uses_one_atomic_eval_and_hides_raw_identity(
    monkeypatch,
):
    """Admission 一次 EVAL 完成，Redis Key 不得出现 Tenant/Subject 原文。"""

    monkeypatch.setenv(
        "AGENT_API_GLOBAL_CONCURRENCY",
        "16",
    )
    monkeypatch.setenv(
        "AGENT_API_TENANT_CONCURRENCY",
        "4",
    )
    monkeypatch.setenv(
        "AGENT_API_SUBJECT_RPM",
        "30",
    )
    monkeypatch.setenv(
        "AGENT_API_TENANT_RPM",
        "120",
    )

    client = _EvalClient()
    backend = RedisSharedTrafficBackend(
        client,
        namespace="commerce:agent:test",
        hash_tag="governed-agent-traffic",
        operation_timeout_seconds=1,
    )

    async def scenario():
        return await backend.admit(
            tenant_id="tenant-west",
            subject="user-secret",
            limits=load_traffic_limits(
                ROOT
            ),
            lease_ttl_ms=90000,
            lease_id="lease-1",
        )

    decision = asyncio.run(
        scenario()
    )
    assert decision.admitted is True
    assert len(
        client.calls
    ) == 1

    script, numkeys, parts = (
        client.calls[0]
    )
    assert script == ADMISSION_LUA
    assert numkeys == 4

    keys = parts[:numkeys]
    joined = "|".join(
        str(item)
        for item in keys
    )
    assert "tenant-west" not in joined
    assert "user-secret" not in joined

    # 四个 Key 都带同一个 Cluster Hash Tag。
    assert all(
        "{governed-agent-traffic}"
        in str(key)
        for key in keys
    )


def test_redis_lua_contains_server_time_sliding_window_and_expiring_lease():
    """Lua 必须使用 Server Time、ZSET Window 和带 TTL 的 Concurrency Lease。"""

    assert (
        "redis.call('TIME')"
        in ADMISSION_LUA
    )
    assert (
        "ZREMRANGEBYSCORE"
        in ADMISSION_LUA
    )
    assert (
        "ZCARD"
        in ADMISSION_LUA
    )
    assert (
        "ZADD"
        in ADMISSION_LUA
    )
    assert (
        "PEXPIRE"
        in ADMISSION_LUA
    )

    assert (
        "redis.call('TIME')"
        in RENEW_LUA
    )
    assert "ZADD" in RENEW_LUA
    assert "ZREM" in RELEASE_LUA


def test_redis_backend_failure_is_explicit_fail_closed():
    """Redis Admission 故障不能静默回退 Local Backend。"""

    backend = RedisSharedTrafficBackend(
        _EvalClient(
            fail=True
        ),
        namespace="commerce:agent:test",
        hash_tag="governed-agent-traffic",
        operation_timeout_seconds=0.1,
    )

    async def scenario():
        with pytest.raises(
            TrafficGuardUnavailable
        ):
            await backend.ready()

    asyncio.run(
        scenario()
    )


class _SharedBackend:
    """在单进程测试中模拟两个 Worker 共享同一 Admission State。"""

    def __init__(self):
        self.active_global = set()
        self.active_tenant = defaultdict(
            set
        )

    async def ready(self):
        """Fake Backend 永远 Ready。"""

        return True

    async def admit(
        self,
        *,
        tenant_id,
        subject,
        limits,
        lease_ttl_ms,
        lease_id,
    ):
        """只模拟本测试关注的共享 Tenant/Global Concurrency。"""

        del (
            subject,
            lease_ttl_ms,
        )

        if (
            len(
                self.active_tenant[
                    tenant_id
                ]
            )
            >= limits.tenant_concurrency
        ):
            return SharedAdmissionDecision(
                False,
                "TENANT_CONCURRENCY_LIMIT",
                1,
            )

        if (
            len(
                self.active_global
            )
            >= limits.global_concurrency
        ):
            return SharedAdmissionDecision(
                False,
                "GLOBAL_CONCURRENCY_LIMIT",
                1,
            )

        self.active_tenant[
            tenant_id
        ].add(
            lease_id
        )
        self.active_global.add(
            lease_id
        )
        return SharedAdmissionDecision(
            True,
            "ADMITTED",
            0,
        )

    async def renew(
        self,
        *,
        tenant_id,
        lease_id,
        lease_ttl_ms,
    ):
        """Fake Lease 存在即续租成功。"""

        del lease_ttl_ms
        return (
            lease_id
            in self.active_tenant[
                tenant_id
            ]
            and lease_id
            in self.active_global
        )

    async def release(
        self,
        *,
        tenant_id,
        lease_id,
    ):
        """两个 Guard 都释放同一 Shared State。"""

        self.active_tenant[
            tenant_id
        ].discard(
            lease_id
        )
        self.active_global.discard(
            lease_id
        )


def test_two_redis_guards_share_tenant_concurrency_budget(
    monkeypatch,
):
    """两个 Worker Guard 必须竞争同一 Tenant Concurrency Budget。"""

    monkeypatch.setenv(
        "AGENT_API_GLOBAL_CONCURRENCY",
        "2",
    )
    monkeypatch.setenv(
        "AGENT_API_TENANT_CONCURRENCY",
        "1",
    )
    monkeypatch.setenv(
        "AGENT_API_SUBJECT_RPM",
        "100",
    )
    monkeypatch.setenv(
        "AGENT_API_TENANT_RPM",
        "100",
    )

    backend = _SharedBackend()
    first_guard = RedisTrafficGuard(
        ROOT,
        backend=backend,
        lease_ttl_seconds=90,
        heartbeat_seconds=20,
    )
    second_guard = RedisTrafficGuard(
        ROOT,
        backend=backend,
        lease_ttl_seconds=90,
        heartbeat_seconds=20,
    )

    async def scenario():
        first = await first_guard.acquire(
            _context(
                subject="user-1"
            )
        )
        try:
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
        finally:
            await first.release_async()

        second = await second_guard.acquire(
            _context(
                subject="user-2"
            )
        )
        await second.release_async()

    asyncio.run(
        scenario()
    )


def test_local_is_default_but_redis_mode_requires_explicit_url(
    monkeypatch,
):
    """默认 Local 保持兼容；显式 Redis 缺 URL 时必须配置失败。"""

    monkeypatch.delenv(
        "AGENT_API_TRAFFIC_BACKEND",
        raising=False,
    )
    local = build_traffic_guard_from_env(
        ROOT
    )
    assert (
        local.backend_name
        == "local"
    )

    monkeypatch.setenv(
        "AGENT_API_TRAFFIC_BACKEND",
        "redis",
    )
    monkeypatch.delenv(
        "AGENT_API_REDIS_URL",
        raising=False,
    )
    with pytest.raises(
        TrafficGuardConfigurationError
    ):
        build_traffic_guard_from_env(
            ROOT
        )


def test_optional_redis_requirement_is_not_forced_into_local_runtime():
    """Redis Client 作为显式共享 Backend Dependency，不污染 Local 基础 Runtime。"""

    base_requirements = (
        ROOT
        / "requirements-agent.txt"
    ).read_text(
        encoding="utf-8"
    )
    redis_requirements = (
        ROOT
        / "requirements-agent-redis.txt"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "redis==" not in
        base_requirements.lower()
    )
    assert (
        "redis==6.4.0"
        in redis_requirements
    )
