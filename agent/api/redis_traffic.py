"""Redis-backed Distributed Traffic Guard（分布式共享容量控制）。

设计目标：
- Subject/Tenant Sliding Window Rate Limit 在 Redis 内原子判断；
- Tenant/Global Concurrency 使用 Expiring Lease（带 TTL 的租约）；
- Lease Heartbeat 让“API 已 504、Worker 仍执行”的请求继续占共享容量；
- Worker/Pod 崩溃后，Lease 会在 TTL 后自动回收；
- Redis Key 不出现原始 tenant_id / subject；
- Admission Script 使用 Redis Server Time，避免 Pod 时钟漂移；
- 所有 Lua KEYS 使用同一个 Redis Cluster Hash Tag，保留单 Slot 原子性。

V1 使用单 Redis Endpoint / Managed Proxy。它不声称自动完成 Redis Cluster
拓扑发现；但 Key Schema 已保持单 Hash Slot 兼容。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.tenancy import RequestContext

from .traffic import (
    AdmissionRejected,
    TrafficGuardConfigurationError,
    TrafficGuardUnavailable,
    TrafficLimits,
    load_traffic_limits,
    load_traffic_policy,
)


# Admission 必须在一个 Lua Script 内完成：
# 1) 清理滑出窗口/过期 Lease；
# 2) 检查 Subject/Tenant RPM；
# 3) 检查 Tenant/Global Concurrency；
# 4) 只有全部通过后才同时写 Rate + Lease。
#
# 返回：
# {1, 0, 0} = ADMITTED
# {0, 1, retry} = SUBJECT_RATE_LIMITED
# {0, 2, retry} = TENANT_RATE_LIMITED
# {0, 3, 1} = TENANT_CONCURRENCY_LIMIT
# {0, 4, 1} = GLOBAL_CONCURRENCY_LIMIT
ADMISSION_LUA = r"""
local subject_limit = tonumber(ARGV[1])
local tenant_limit = tonumber(ARGV[2])
local tenant_concurrency_limit = tonumber(ARGV[3])
local global_concurrency_limit = tonumber(ARGV[4])
local window_ms = tonumber(ARGV[5])
local lease_ttl_ms = tonumber(ARGV[6])
local lease_id = ARGV[7]

local server_time = redis.call('TIME')
local now_ms = (
  tonumber(server_time[1]) * 1000
  + math.floor(tonumber(server_time[2]) / 1000)
)
local rate_cutoff = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', rate_cutoff)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', rate_cutoff)
redis.call('ZREMRANGEBYSCORE', KEYS[3], '-inf', now_ms)
redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now_ms)

local subject_count = redis.call('ZCARD', KEYS[1])
if subject_count >= subject_limit then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry = 1
  if oldest[2] then
    retry = math.max(
      1,
      math.ceil(
        (tonumber(oldest[2]) + window_ms - now_ms) / 1000
      )
    )
  end
  return {0, 1, retry}
end

local tenant_rate_count = redis.call('ZCARD', KEYS[2])
if tenant_rate_count >= tenant_limit then
  local oldest = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
  local retry = 1
  if oldest[2] then
    retry = math.max(
      1,
      math.ceil(
        (tonumber(oldest[2]) + window_ms - now_ms) / 1000
      )
    )
  end
  return {0, 2, retry}
end

local tenant_active = redis.call('ZCARD', KEYS[3])
if tenant_active >= tenant_concurrency_limit then
  return {0, 3, 1}
end

local global_active = redis.call('ZCARD', KEYS[4])
if global_active >= global_concurrency_limit then
  return {0, 4, 1}
end

local lease_expiry_ms = now_ms + lease_ttl_ms

redis.call('ZADD', KEYS[1], now_ms, lease_id)
redis.call('ZADD', KEYS[2], now_ms, lease_id)
redis.call('PEXPIRE', KEYS[1], window_ms + 1000)
redis.call('PEXPIRE', KEYS[2], window_ms + 1000)

redis.call('ZADD', KEYS[3], lease_expiry_ms, lease_id)
redis.call('ZADD', KEYS[4], lease_expiry_ms, lease_id)
redis.call('PEXPIRE', KEYS[3], lease_ttl_ms * 2)
redis.call('PEXPIRE', KEYS[4], lease_ttl_ms * 2)

return {1, 0, 0}
"""


RENEW_LUA = r"""
local lease_ttl_ms = tonumber(ARGV[1])
local lease_id = ARGV[2]

local tenant_score = redis.call('ZSCORE', KEYS[1], lease_id)
local global_score = redis.call('ZSCORE', KEYS[2], lease_id)
if not tenant_score or not global_score then
  return {0, 0}
end

local server_time = redis.call('TIME')
local now_ms = (
  tonumber(server_time[1]) * 1000
  + math.floor(tonumber(server_time[2]) / 1000)
)
local lease_expiry_ms = now_ms + lease_ttl_ms

redis.call('ZADD', KEYS[1], 'XX', lease_expiry_ms, lease_id)
redis.call('ZADD', KEYS[2], 'XX', lease_expiry_ms, lease_id)
redis.call('PEXPIRE', KEYS[1], lease_ttl_ms * 2)
redis.call('PEXPIRE', KEYS[2], lease_ttl_ms * 2)

return {1, lease_expiry_ms}
"""


RELEASE_LUA = r"""
local lease_id = ARGV[1]
redis.call('ZREM', KEYS[1], lease_id)
redis.call('ZREM', KEYS[2], lease_id)
return 1
"""


_REDIS_NAMESPACE_RE = re.compile(
    r"^[A-Za-z0-9:_-]{1,120}$"
)


@dataclass(frozen=True)
class SharedAdmissionDecision:
    """Redis Admission Script 的强类型结果。"""

    admitted: bool
    code: str
    retry_after_seconds: int


class RedisSharedTrafficBackend:
    """负责 Redis Key Schema、Lua 原子操作与底层异常收口。"""

    def __init__(
        self,
        client: Any,
        *,
        namespace: str,
        hash_tag: str,
        operation_timeout_seconds: float,
    ):
        if not _REDIS_NAMESPACE_RE.fullmatch(
            namespace
        ):
            raise TrafficGuardConfigurationError(
                "AGENT_API_REDIS_NAMESPACE contains unsupported characters."
            )
        if not _REDIS_NAMESPACE_RE.fullmatch(
            hash_tag
        ):
            raise TrafficGuardConfigurationError(
                "Redis traffic hash tag contains unsupported characters."
            )

        self.client = client
        self.namespace = namespace
        self.hash_tag = hash_tag
        self.operation_timeout_seconds = (
            operation_timeout_seconds
        )

    async def ready(self) -> bool:
        """PING 共享 Redis；异常只投影为 Generic Backend Unavailable。"""

        try:
            result = await asyncio.wait_for(
                self.client.ping(),
                timeout=(
                    self.operation_timeout_seconds
                ),
            )
        except Exception as exc:
            raise TrafficGuardUnavailable(
                "Shared traffic backend is unavailable."
            ) from exc
        return bool(result)

    async def admit(
        self,
        *,
        tenant_id: str,
        subject: str,
        limits: TrafficLimits,
        lease_ttl_ms: int,
        lease_id: str,
    ) -> SharedAdmissionDecision:
        """使用一个 Redis EVAL 原子完成 Rate + Concurrency Admission。"""

        keys = self._admission_keys(
            tenant_id=tenant_id,
            subject=subject,
        )
        args = (
            limits.subject_rpm,
            limits.tenant_rpm,
            limits.tenant_concurrency,
            limits.global_concurrency,
            limits.window_seconds * 1000,
            lease_ttl_ms,
            lease_id,
        )

        try:
            response = await asyncio.wait_for(
                self.client.eval(
                    ADMISSION_LUA,
                    len(keys),
                    *keys,
                    *args,
                ),
                timeout=(
                    self.operation_timeout_seconds
                ),
            )
        except Exception as exc:
            raise TrafficGuardUnavailable(
                "Shared traffic backend admission failed."
            ) from exc

        return self._parse_admission(
            response
        )

    async def renew(
        self,
        *,
        tenant_id: str,
        lease_id: str,
        lease_ttl_ms: int,
    ) -> bool:
        """续租 Tenant + Global Concurrency Lease；缺任一 Lease 即视为失效。"""

        tenant_key = self._tenant_concurrency_key(
            tenant_id
        )
        global_key = self._global_concurrency_key()

        try:
            response = await asyncio.wait_for(
                self.client.eval(
                    RENEW_LUA,
                    2,
                    tenant_key,
                    global_key,
                    lease_ttl_ms,
                    lease_id,
                ),
                timeout=(
                    self.operation_timeout_seconds
                ),
            )
        except Exception as exc:
            raise TrafficGuardUnavailable(
                "Shared traffic backend lease renewal failed."
            ) from exc

        values = list(
            response or ()
        )
        return bool(
            values
            and int(values[0]) == 1
        )

    async def release(
        self,
        *,
        tenant_id: str,
        lease_id: str,
    ) -> None:
        """显式释放并发 Lease；Rate Window 不因请求结束而回滚。"""

        tenant_key = self._tenant_concurrency_key(
            tenant_id
        )
        global_key = self._global_concurrency_key()

        try:
            await asyncio.wait_for(
                self.client.eval(
                    RELEASE_LUA,
                    2,
                    tenant_key,
                    global_key,
                    lease_id,
                ),
                timeout=(
                    self.operation_timeout_seconds
                ),
            )
        except Exception as exc:
            # Release 失败不会把已完成 Worker 重新执行；
            # Expiring Lease 会在 TTL 后自动清理，属于保守性容量占用。
            raise TrafficGuardUnavailable(
                "Shared traffic backend lease release failed."
            ) from exc

    def _admission_keys(
        self,
        *,
        tenant_id: str,
        subject: str,
    ) -> tuple[str, str, str, str]:
        """生成不含原始身份的 Redis KEYS；四个 Key 强制落在同一 Hash Slot。"""

        tenant_digest = self._digest(
            tenant_id
        )
        subject_digest = self._digest(
            tenant_id
            + "\x00"
            + subject
        )
        prefix = self._prefix()

        return (
            (
                f"{prefix}:rate:subject:"
                f"{subject_digest}"
            ),
            (
                f"{prefix}:rate:tenant:"
                f"{tenant_digest}"
            ),
            (
                f"{prefix}:concurrency:tenant:"
                f"{tenant_digest}"
            ),
            (
                f"{prefix}:concurrency:global"
            ),
        )

    def _tenant_concurrency_key(
        self,
        tenant_id: str,
    ) -> str:
        """返回 Tenant Concurrency Key；Tenant 原文不会进入 Redis Key。"""

        return (
            f"{self._prefix()}:concurrency:tenant:"
            f"{self._digest(tenant_id)}"
        )

    def _global_concurrency_key(
        self,
    ) -> str:
        """返回所有 Worker / Pod 共享的 Global Concurrency Key。"""

        return (
            f"{self._prefix()}:concurrency:global"
        )

    def _prefix(self) -> str:
        """使用 Redis Cluster Hash Tag 确保 Lua 多 Key 仍处于单 Slot。"""

        return (
            f"{self.namespace}:"
            f"{{{self.hash_tag}}}"
        )

    @staticmethod
    def _digest(value: str) -> str:
        """稳定哈希身份维度，避免 Redis Key 暴露 tenant/subject 原文。"""

        return hashlib.sha256(
            value.encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _parse_admission(
        response: Any,
    ) -> SharedAdmissionDecision:
        """把 Lua 数字状态码转换成稳定 API Admission Code。"""

        values = list(
            response or ()
        )
        if len(values) < 3:
            raise TrafficGuardUnavailable(
                "Shared traffic backend returned an invalid admission response."
            )

        admitted = (
            int(values[0]) == 1
        )
        reason = int(
            values[1]
        )
        retry = max(
            0,
            int(values[2]),
        )

        if admitted:
            return SharedAdmissionDecision(
                admitted=True,
                code="ADMITTED",
                retry_after_seconds=0,
            )

        code_map = {
            1: "SUBJECT_RATE_LIMITED",
            2: "TENANT_RATE_LIMITED",
            3: "TENANT_CONCURRENCY_LIMIT",
            4: "GLOBAL_CONCURRENCY_LIMIT",
        }
        code = code_map.get(
            reason
        )
        if code is None:
            raise TrafficGuardUnavailable(
                "Shared traffic backend returned an unknown admission code."
            )

        return SharedAdmissionDecision(
            admitted=False,
            code=code,
            retry_after_seconds=max(
                1,
                retry,
            ),
        )


@dataclass
class RedisAdmissionLease:
    """带 Heartbeat 的 Redis Expiring Concurrency Lease。"""

    backend: RedisSharedTrafficBackend
    tenant_id: str
    lease_id: str
    lease_ttl_ms: int
    heartbeat_seconds: float
    _released: bool = False
    _healthy: bool = True
    _heartbeat_task: asyncio.Task | None = field(
        default=None,
        init=False,
        repr=False,
    )

    @property
    def healthy(self) -> bool:
        """Heartbeat 未丢失且 Lease 尚未释放时才允许正常返回答案。"""

        return (
            self._healthy
            and not self._released
        )

    def start(self) -> None:
        """Admission 成功后启动 Lease Heartbeat。"""

        if self._heartbeat_task is None:
            self._heartbeat_task = (
                asyncio.create_task(
                    self._heartbeat()
                )
            )

    async def _heartbeat(self) -> None:
        """定期续租；任何续租异常/Lease 丢失都会标记不健康。"""

        try:
            while not self._released:
                await asyncio.sleep(
                    self.heartbeat_seconds
                )
                if self._released:
                    return

                renewed = await self.backend.renew(
                    tenant_id=(
                        self.tenant_id
                    ),
                    lease_id=(
                        self.lease_id
                    ),
                    lease_ttl_ms=(
                        self.lease_ttl_ms
                    ),
                )
                if not renewed:
                    self._healthy = False
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # 不保存底层 Redis URL / Credential / Exception Text。
            self._healthy = False

    async def release_async(self) -> None:
        """取消 Heartbeat 并显式 ZREM Lease；失败时依赖 TTL 保守回收。"""

        if self._released:
            return
        self._released = True

        task = self._heartbeat_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await self.backend.release(
            tenant_id=self.tenant_id,
            lease_id=self.lease_id,
        )


class RedisTrafficGuard:
    """跨 Worker / Pod 共用 Redis 状态的 Traffic Guard。"""

    backend_name = "redis"

    def __init__(
        self,
        project_root: Path | str,
        *,
        backend: RedisSharedTrafficBackend,
        lease_ttl_seconds: float,
        heartbeat_seconds: float,
    ):
        self.root = Path(
            project_root
        ).resolve()
        self.limits = load_traffic_limits(
            self.root
        )
        self.request_timeout_seconds = (
            self.limits.request_timeout_seconds
        )
        self.backend = backend
        self.lease_ttl_seconds = (
            lease_ttl_seconds
        )
        self.heartbeat_seconds = (
            heartbeat_seconds
        )

        if (
            self.heartbeat_seconds
            >= self.lease_ttl_seconds / 2
        ):
            raise TrafficGuardConfigurationError(
                "Redis lease heartbeat must be less than half of lease TTL."
            )

        self.lease_ttl_ms = int(
            self.lease_ttl_seconds
            * 1000
        )

    @classmethod
    def from_env(
        cls,
        project_root: Path | str,
    ) -> "RedisTrafficGuard":
        """从 Env 构造 Redis Client；URL/Password 不写入日志或异常。"""

        root = Path(
            project_root
        ).resolve()
        policy = load_traffic_policy(
            root
        )
        config = policy[
            "distributed"
        ]["redis"]

        url = os.getenv(
            str(config["url_env"]),
            "",
        ).strip()
        if not url:
            raise TrafficGuardConfigurationError(
                "Redis traffic backend requires AGENT_API_REDIS_URL."
            )

        namespace = os.getenv(
            str(config["namespace_env"]),
            str(config["default_namespace"]),
        ).strip()

        lease_ttl_seconds = _env_float(
            config["lease_ttl_seconds"],
            minimum=10.0,
            maximum=3600.0,
        )
        heartbeat_seconds = _env_float(
            config["heartbeat_seconds"],
            minimum=1.0,
            maximum=600.0,
        )
        operation_timeout_seconds = _env_float(
            config["operation_timeout_seconds"],
            minimum=0.05,
            maximum=10.0,
        )

        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise TrafficGuardConfigurationError(
                "Redis traffic backend requires requirements-agent-redis.txt."
            ) from exc

        try:
            client = redis.from_url(
                url,
                decode_responses=True,
                socket_connect_timeout=(
                    operation_timeout_seconds
                ),
                socket_timeout=(
                    operation_timeout_seconds
                ),
                health_check_interval=30,
            )
        except Exception as exc:
            raise TrafficGuardConfigurationError(
                "Redis traffic client could not be configured."
            ) from exc

        backend = RedisSharedTrafficBackend(
            client,
            namespace=namespace,
            hash_tag=str(
                config["cluster_hash_tag"]
            ),
            operation_timeout_seconds=(
                operation_timeout_seconds
            ),
        )
        return cls(
            root,
            backend=backend,
            lease_ttl_seconds=(
                lease_ttl_seconds
            ),
            heartbeat_seconds=(
                heartbeat_seconds
            ),
        )

    async def ready(self) -> bool:
        """Readiness 必须真实 PING 共享 Redis，而不是只检查 URL 是否存在。"""

        return await self.backend.ready()

    async def acquire(
        self,
        context: RequestContext,
    ) -> RedisAdmissionLease:
        """在 Redis 原子 Admission 后返回带 Heartbeat 的共享 Lease。"""

        tenant_id = str(
            context.tenant_id or ""
        ).strip()
        subject = str(
            context.subject or ""
        ).strip()
        if not tenant_id or not subject:
            raise AdmissionRejected(
                code="INVALID_REQUEST_CONTEXT",
                retry_after_seconds=1,
            )

        lease_id = str(
            uuid4()
        )
        decision = await self.backend.admit(
            tenant_id=tenant_id,
            subject=subject,
            limits=self.limits,
            lease_ttl_ms=(
                self.lease_ttl_ms
            ),
            lease_id=lease_id,
        )

        if not decision.admitted:
            raise AdmissionRejected(
                code=decision.code,
                retry_after_seconds=(
                    decision.retry_after_seconds
                ),
            )

        lease = RedisAdmissionLease(
            backend=self.backend,
            tenant_id=tenant_id,
            lease_id=lease_id,
            lease_ttl_ms=(
                self.lease_ttl_ms
            ),
            heartbeat_seconds=(
                self.heartbeat_seconds
            ),
        )
        lease.start()
        return lease


def _env_float(
    config: dict,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """解析 Redis Backend 专属的 Float Env。"""

    raw = os.getenv(
        str(config["env"]),
        str(config["default"]),
    ).strip()
    try:
        value = float(
            raw
        )
    except ValueError as exc:
        raise TrafficGuardConfigurationError(
            f"{config['env']} must be numeric."
        ) from exc

    if not minimum <= value <= maximum:
        raise TrafficGuardConfigurationError(
            f"{config['env']} must stay within [{minimum}, {maximum}]."
        )
    return value
