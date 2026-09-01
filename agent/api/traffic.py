"""Agent API 的 process-local SLO Guard（进程内流量保护）。

V1 解决四个生产入口问题：
1. Subject / Tenant 滑动窗口限流；
2. Tenant / Global 并发上限；
3. 不排队，容量不足立即拒绝；
4. API 请求超时后，后台 Worker 仍占用并发槽直到真正结束。

重要边界：
- 这是单进程 Guard，不伪装成集群级限流；
- 多 Worker / 多 Pod 的全局限流需要 API Gateway、Redis 等共享状态；
- 当前数值是初始 Guardrail（护栏），不是压测后得到的正式 SLO。
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque

import yaml

from agent.tenancy import RequestContext


class TrafficGuardConfigurationError(RuntimeError):
    """流量保护配置不合法。"""


@dataclass(frozen=True)
class AdmissionRejected(RuntimeError):
    """一次请求在进入 Agent Runtime 前被容量策略拒绝。"""

    code: str
    retry_after_seconds: int

    def __str__(self) -> str:
        return self.code


@dataclass
class AdmissionLease:
    """一次成功 Admission 对应的并发槽；释放操作必须幂等。"""

    guard: "GovernedTrafficGuard"
    tenant_id: str
    _released: bool = False

    def release(self) -> None:
        """Worker 真正结束后释放 Global + Tenant 并发槽。"""

        if self._released:
            return
        self._released = True
        self.guard._release(self.tenant_id)


class GovernedTrafficGuard:
    """基于可信 RequestContext 做限流和并发 Admission。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/agent_runtime_slo_policy.yml").read_text(
                encoding="utf-8"
            )
        )

        limits = self.policy["limits"]
        self.request_timeout_seconds = self._float_env(
            limits["request_timeout_seconds"],
            minimum=0.05,
            maximum=600.0,
        )
        self.global_concurrency = self._int_env(
            limits["global_concurrency"],
            minimum=1,
            maximum=10000,
        )
        self.tenant_concurrency = self._int_env(
            limits["tenant_concurrency"],
            minimum=1,
            maximum=self.global_concurrency,
        )
        self.subject_rpm = self._int_env(
            limits["subject_requests_per_minute"],
            minimum=1,
            maximum=1_000_000,
        )
        self.tenant_rpm = self._int_env(
            limits["tenant_requests_per_minute"],
            minimum=1,
            maximum=1_000_000,
        )
        self.max_tracked_keys = self._int_env(
            limits["max_tracked_identity_keys"],
            minimum=100,
            maximum=1_000_000,
        )
        self.window_seconds = int(self.policy["rate_limit"]["window_seconds"])

        self._lock = asyncio.Lock()
        self._subject_windows: dict[tuple[str, str], Deque[float]] = {}
        self._tenant_windows: dict[str, Deque[float]] = {}

        self._global_active = 0
        self._tenant_active: dict[str, int] = {}
        self._admissions = 0

    async def acquire(self, context: RequestContext) -> AdmissionLease:
        """无排队尝试 Admission；超过 Rate/Concurrency 立即拒绝。"""

        tenant_id = str(context.tenant_id or "").strip()
        subject = str(context.subject or "").strip()
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

            subject_key = (tenant_id, subject)
            self._ensure_tracking_capacity(subject_key, tenant_id, now)

            subject_window = self._subject_windows.setdefault(
                subject_key,
                deque(),
            )
            tenant_window = self._tenant_windows.setdefault(
                tenant_id,
                deque(),
            )
            self._prune(subject_window, now)
            self._prune(tenant_window, now)

            if len(subject_window) >= self.subject_rpm:
                raise AdmissionRejected(
                    code="SUBJECT_RATE_LIMITED",
                    retry_after_seconds=self._retry_after(subject_window, now),
                )
            if len(tenant_window) >= self.tenant_rpm:
                raise AdmissionRejected(
                    code="TENANT_RATE_LIMITED",
                    retry_after_seconds=self._retry_after(tenant_window, now),
                )

            tenant_active = self._tenant_active.get(tenant_id, 0)
            if tenant_active >= self.tenant_concurrency:
                raise AdmissionRejected(
                    code="TENANT_CONCURRENCY_LIMIT",
                    retry_after_seconds=1,
                )
            if self._global_active >= self.global_concurrency:
                raise AdmissionRejected(
                    code="GLOBAL_CONCURRENCY_LIMIT",
                    retry_after_seconds=1,
                )

            # 只有真正被 Admission 的请求才占用 RPM 配额和并发槽。
            subject_window.append(now)
            tenant_window.append(now)
            self._global_active += 1
            self._tenant_active[tenant_id] = tenant_active + 1

            return AdmissionLease(
                guard=self,
                tenant_id=tenant_id,
            )

    def snapshot(self) -> dict[str, object]:
        """返回不包含用户 Prompt 的轻量容量快照，主要用于测试和内部诊断。"""

        return {
            "global_active": self._global_active,
            "tenant_active": dict(self._tenant_active),
            "global_concurrency": self.global_concurrency,
            "tenant_concurrency": self.tenant_concurrency,
        }

    def _release(self, tenant_id: str) -> None:
        """释放并发计数；由 Event Loop 的 Task Done Callback 调用。"""

        self._global_active = max(0, self._global_active - 1)

        active = max(0, self._tenant_active.get(tenant_id, 0) - 1)
        if active:
            self._tenant_active[tenant_id] = active
        else:
            self._tenant_active.pop(tenant_id, None)

    def _ensure_tracking_capacity(
        self,
        subject_key: tuple[str, str],
        tenant_id: str,
        now: float,
    ) -> None:
        """限制内存中的 Tenant/Subject Key 数量，避免身份基数无限增长。"""

        if (
            subject_key in self._subject_windows
            and tenant_id in self._tenant_windows
        ):
            return

        self._cleanup_stale_windows(now)
        tracked = len(self._subject_windows) + len(self._tenant_windows)
        if tracked >= self.max_tracked_keys:
            raise AdmissionRejected(
                code="RATE_LIMIT_TRACKING_CAPACITY",
                retry_after_seconds=self.window_seconds,
            )

    def _cleanup_stale_windows(self, now: float) -> None:
        """清理一个窗口期内没有请求的 Rate-Limit Key。"""

        for key, window in list(self._subject_windows.items()):
            self._prune(window, now)
            if not window:
                self._subject_windows.pop(key, None)

        for key, window in list(self._tenant_windows.items()):
            self._prune(window, now)
            if not window:
                self._tenant_windows.pop(key, None)

    def _prune(self, window: Deque[float], now: float) -> None:
        """移除已经滑出当前限流窗口的时间戳。"""

        cutoff = now - self.window_seconds
        while window and window[0] <= cutoff:
            window.popleft()

    def _retry_after(self, window: Deque[float], now: float) -> int:
        """计算客户端最早可以重试的秒数。"""

        if not window:
            return 1
        remaining = self.window_seconds - (now - window[0])
        return max(1, int(remaining) + 1)

    @staticmethod
    def _raw_env(config: dict) -> str:
        """读取一个 policy 定义的环境变量，否则使用仓库默认值。"""

        return os.getenv(
            str(config["env"]),
            str(config["default"]),
        ).strip()

    @classmethod
    def _int_env(
        cls,
        config: dict,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        """解析并校验整数 Guardrail。"""

        raw = cls._raw_env(config)
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

    @classmethod
    def _float_env(
        cls,
        config: dict,
        *,
        minimum: float,
        maximum: float,
    ) -> float:
        """解析并校验浮点超时 Guardrail。"""

        raw = cls._raw_env(config)
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
