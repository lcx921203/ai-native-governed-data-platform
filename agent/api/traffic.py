"""Agent API Traffic Guard（流量保护）的共享契约与本地实现。

V2 将“策略”与“状态存储”拆开：
- ``GovernedTrafficGuard``：单进程 Local Backend，保留开发/单 Worker 兼容；
- ``RedisTrafficGuard``：由 Factory 按需加载，提供跨 Worker / Pod 的共享状态；
- 两种实现都遵守相同的 Subject/Tenant RPM、Tenant/Global Concurrency 和 Timeout 契约。

重要边界：
- Local Backend 只能代表当前 Python 进程；
- 多 Worker / 多 Pod 必须显式启用 Redis Backend；
- 当前数值仍是 Initial Guardrail（初始护栏），不是压测后得到的正式 SLO。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Protocol

import yaml

from agent.tenancy import RequestContext


class TrafficGuardConfigurationError(RuntimeError):
    """流量保护配置不合法。"""


class TrafficGuardUnavailable(RuntimeError):
    """共享 Traffic Backend 暂时不可用；生产入口必须 Fail Closed。"""


@dataclass(frozen=True)
class AdmissionRejected(RuntimeError):
    """一次请求在进入 Agent Runtime 前被容量策略拒绝。"""

    code: str
    retry_after_seconds: int

    def __str__(self) -> str:
        """返回稳定错误码，避免把内部状态拼进公共错误。"""

        return self.code


@dataclass(frozen=True)
class TrafficLimits:
    """Local / Redis Backend 共用的受治理流量上限。"""

    request_timeout_seconds: float
    global_concurrency: int
    tenant_concurrency: int
    subject_rpm: int
    tenant_rpm: int
    max_tracked_keys: int
    window_seconds: int


class TrafficLease(Protocol):
    """一次 Admission 的并发 Lease 契约。"""

    @property
    def healthy(self) -> bool:
        """共享 Lease 是否仍保持有效。"""

    async def release_async(self) -> None:
        """Worker 真正结束后释放并发 Lease。"""


class TrafficGuard(Protocol):
    """FastAPI 只依赖这个最小 Traffic Guard Protocol。"""

    request_timeout_seconds: float
    backend_name: str

    async def acquire(self, context: RequestContext) -> TrafficLease:
        """无排队尝试 Admission。"""

    async def ready(self) -> bool:
        """检查当前 Backend 是否可以安全接受新请求。"""


def load_traffic_policy(project_root: Path | str) -> dict:
    """读取统一 SLO / Traffic Policy。"""

    root = Path(project_root).resolve()
    return yaml.safe_load(
        (
            root
            / "agent/contracts/agent_runtime_slo_policy.yml"
        ).read_text(encoding="utf-8")
    )


def _raw_env(config: dict) -> str:
    """读取 Policy 定义的环境变量，否则使用仓库默认值。"""

    return os.getenv(
        str(config["env"]),
        str(config["default"]),
    ).strip()


def _int_env(
    config: dict,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """解析并校验整数 Guardrail。"""

    raw = _raw_env(config)
    try:
        value = int(raw)
    except ValueError as exc:
        raise TrafficGuardConfigurationError(
            f"{config['env']} must be an integer."
        ) from exc

    if not minimum <= value <= maximum:
        raise TrafficGuardConfigurationError(
            f"{config['env']} must stay within [{minimum}, {maximum}]."
        )
    return value


def _float_env(
    config: dict,
    *,
    minimum: float,
    maximum: float,
) -> float:
    """解析并校验浮点 Guardrail。"""

    raw = _raw_env(config)
    try:
        value = float(raw)
    except ValueError as exc:
        raise TrafficGuardConfigurationError(
            f"{config['env']} must be numeric."
        ) from exc

    if not minimum <= value <= maximum:
        raise TrafficGuardConfigurationError(
            f"{config['env']} must stay within [{minimum}, {maximum}]."
        )
    return value


def load_traffic_limits(project_root: Path | str) -> TrafficLimits:
    """把 YAML + Env 解析成 Local / Redis 共用的强类型限制。"""

    policy = load_traffic_policy(project_root)
    limits = policy["limits"]

    global_concurrency = _int_env(
        limits["global_concurrency"],
        minimum=1,
        maximum=10000,
    )
    tenant_concurrency = _int_env(
        limits["tenant_concurrency"],
        minimum=1,
        maximum=global_concurrency,
    )

    return TrafficLimits(
        request_timeout_seconds=_float_env(
            limits["request_timeout_seconds"],
            minimum=0.05,
            maximum=600.0,
        ),
        global_concurrency=global_concurrency,
        tenant_concurrency=tenant_concurrency,
        subject_rpm=_int_env(
            limits["subject_requests_per_minute"],
            minimum=1,
            maximum=1_000_000,
        ),
        tenant_rpm=_int_env(
            limits["tenant_requests_per_minute"],
            minimum=1,
            maximum=1_000_000,
        ),
        max_tracked_keys=_int_env(
            limits["max_tracked_identity_keys"],
            minimum=100,
            maximum=1_000_000,
        ),
        window_seconds=int(
            policy["rate_limit"]["window_seconds"]
        ),
    )


@dataclass
class AdmissionLease:
    """Local Backend 的并发 Lease；Release 保持幂等。"""

    guard: "GovernedTrafficGuard"
    tenant_id: str
    _released: bool = False

    @property
    def healthy(self) -> bool:
        """Local Lease 不依赖外部共享存储，只要未释放就视为有效。"""

        return not self._released

    def release(self) -> None:
        """兼容已有 Local 测试：同步释放当前进程的并发槽。"""

        if self._released:
            return
        self._released = True
        self.guard._release_now(self.tenant_id)

    async def release_async(self) -> None:
        """统一异步接口；Local Backend 内部无需网络 I/O。"""

        self.release()


class GovernedTrafficGuard:
    """Process-local Traffic Guard；只适合单 Worker / 单进程边界。"""

    backend_name = "local"

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = load_traffic_policy(self.root)
        self.limits = load_traffic_limits(self.root)

        self.request_timeout_seconds = (
            self.limits.request_timeout_seconds
        )
        self.global_concurrency = (
            self.limits.global_concurrency
        )
        self.tenant_concurrency = (
            self.limits.tenant_concurrency
        )
        self.subject_rpm = self.limits.subject_rpm
        self.tenant_rpm = self.limits.tenant_rpm
        self.max_tracked_keys = (
            self.limits.max_tracked_keys
        )
        self.window_seconds = (
            self.limits.window_seconds
        )

        self._lock = asyncio.Lock()
        self._subject_windows: dict[
            tuple[str, str],
            Deque[float],
        ] = {}
        self._tenant_windows: dict[
            str,
            Deque[float],
        ] = {}

        self._global_active = 0
        self._tenant_active: dict[str, int] = {}
        self._admissions = 0

    async def ready(self) -> bool:
        """Local Backend 没有外部依赖，构造成功即 Ready。"""

        return True

    async def acquire(
        self,
        context: RequestContext,
    ) -> AdmissionLease:
        """无排队尝试 Admission；超过 Rate/Concurrency 立即拒绝。"""

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

        async with self._lock:
            now = time.monotonic()
            self._admissions += 1
            if self._admissions % 100 == 0:
                self._cleanup_stale_windows(now)

            subject_key = (
                tenant_id,
                subject,
            )
            self._ensure_tracking_capacity(
                subject_key,
                tenant_id,
                now,
            )

            subject_window = (
                self._subject_windows.setdefault(
                    subject_key,
                    deque(),
                )
            )
            tenant_window = (
                self._tenant_windows.setdefault(
                    tenant_id,
                    deque(),
                )
            )
            self._prune(
                subject_window,
                now,
            )
            self._prune(
                tenant_window,
                now,
            )

            if (
                len(subject_window)
                >= self.subject_rpm
            ):
                raise AdmissionRejected(
                    code="SUBJECT_RATE_LIMITED",
                    retry_after_seconds=self._retry_after(
                        subject_window,
                        now,
                    ),
                )

            if (
                len(tenant_window)
                >= self.tenant_rpm
            ):
                raise AdmissionRejected(
                    code="TENANT_RATE_LIMITED",
                    retry_after_seconds=self._retry_after(
                        tenant_window,
                        now,
                    ),
                )

            tenant_active = (
                self._tenant_active.get(
                    tenant_id,
                    0,
                )
            )
            if (
                tenant_active
                >= self.tenant_concurrency
            ):
                raise AdmissionRejected(
                    code="TENANT_CONCURRENCY_LIMIT",
                    retry_after_seconds=1,
                )

            if (
                self._global_active
                >= self.global_concurrency
            ):
                raise AdmissionRejected(
                    code="GLOBAL_CONCURRENCY_LIMIT",
                    retry_after_seconds=1,
                )

            # 只有真正 Admission 的请求才占用 RPM 配额和并发槽。
            subject_window.append(now)
            tenant_window.append(now)
            self._global_active += 1
            self._tenant_active[
                tenant_id
            ] = tenant_active + 1

            return AdmissionLease(
                guard=self,
                tenant_id=tenant_id,
            )

    def snapshot(self) -> dict[str, object]:
        """返回不包含 Prompt 的轻量 Local 容量快照。"""

        return {
            "backend": self.backend_name,
            "global_active": self._global_active,
            "tenant_active": dict(
                self._tenant_active
            ),
            "global_concurrency": (
                self.global_concurrency
            ),
            "tenant_concurrency": (
                self.tenant_concurrency
            ),
        }

    def _release_now(
        self,
        tenant_id: str,
    ) -> None:
        """同步释放 Local 并发计数；Event Loop 内不会跨线程修改。"""

        self._global_active = max(
            0,
            self._global_active - 1,
        )

        active = max(
            0,
            self._tenant_active.get(
                tenant_id,
                0,
            )
            - 1,
        )
        if active:
            self._tenant_active[
                tenant_id
            ] = active
        else:
            self._tenant_active.pop(
                tenant_id,
                None,
            )

    def _ensure_tracking_capacity(
        self,
        subject_key: tuple[str, str],
        tenant_id: str,
        now: float,
    ) -> None:
        """限制进程内 Rate-Limit Key 数量，防止身份基数无限增长。"""

        if (
            subject_key
            in self._subject_windows
            and tenant_id
            in self._tenant_windows
        ):
            return

        self._cleanup_stale_windows(now)
        tracked = (
            len(self._subject_windows)
            + len(self._tenant_windows)
        )
        if tracked >= self.max_tracked_keys:
            raise AdmissionRejected(
                code="RATE_LIMIT_TRACKING_CAPACITY",
                retry_after_seconds=(
                    self.window_seconds
                ),
            )

    def _cleanup_stale_windows(
        self,
        now: float,
    ) -> None:
        """清理一个窗口期内没有请求的 Local Rate-Limit Key。"""

        for key, window in list(
            self._subject_windows.items()
        ):
            self._prune(
                window,
                now,
            )
            if not window:
                self._subject_windows.pop(
                    key,
                    None,
                )

        for key, window in list(
            self._tenant_windows.items()
        ):
            self._prune(
                window,
                now,
            )
            if not window:
                self._tenant_windows.pop(
                    key,
                    None,
                )

    def _prune(
        self,
        window: Deque[float],
        now: float,
    ) -> None:
        """移除已经滑出 Local Rate-Limit Window 的时间戳。"""

        cutoff = (
            now
            - self.window_seconds
        )
        while (
            window
            and window[0] <= cutoff
        ):
            window.popleft()

    def _retry_after(
        self,
        window: Deque[float],
        now: float,
    ) -> int:
        """计算客户端最早可以重试的秒数。"""

        if not window:
            return 1
        remaining = (
            self.window_seconds
            - (now - window[0])
        )
        return max(
            1,
            int(remaining) + 1,
        )


def build_traffic_guard_from_env(
    project_root: Path | str,
) -> TrafficGuard:
    """根据受治理 Backend Policy 构造 Local 或 Redis Traffic Guard。

    ``redis`` 模式使用惰性导入，因此普通单 Worker 开发和 CI 不需要安装
    Redis Client；只有明确启用共享 Backend 时才需要
    ``requirements-agent-redis.txt``。
    """

    root = Path(project_root).resolve()
    policy = load_traffic_policy(root)
    config = policy["backend"]
    backend = os.getenv(
        str(config["mode_env"]),
        str(config["default_mode"]),
    ).strip().lower()

    allowed = {
        str(item)
        for item in config["allowed_modes"]
    }
    if backend not in allowed:
        raise TrafficGuardConfigurationError(
            "Unsupported Agent API traffic backend."
        )

    if backend == "local":
        return GovernedTrafficGuard(root)

    if backend == "redis":
        from .redis_traffic import (
            RedisTrafficGuard,
        )

        return RedisTrafficGuard.from_env(
            root
        )

    raise TrafficGuardConfigurationError(
        "No governed Agent API traffic backend matched."
    )
