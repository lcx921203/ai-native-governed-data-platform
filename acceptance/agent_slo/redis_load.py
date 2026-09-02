"""Redis Admission Layer 的可重复 Load Test Harness。

这个 Harness 测的是 RequestContext -> RedisTrafficGuard.acquire -> Lua Admission
-> Concurrency Lease -> release。它不调用真实 LLM、MetricFlow、Trino 或外部 Tool，
因此输出只能称为 REDIS_ADMISSION_LOAD_OBSERVATION，不能直接称为端到端 Agent SLO。
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Iterable
from uuid import uuid4

import yaml

from agent.api.redis_traffic import RedisTrafficGuard
from agent.api.traffic import AdmissionRejected, TrafficGuardUnavailable
from agent.tenancy import RequestContext


@dataclass(frozen=True)
class LoadScenario:
    """一个有界 Redis Admission Load Scenario。"""

    name: str
    attempts: int
    workers: int
    hold_ms: float
    tenant_count: int
    subject_count: int
    global_concurrency: int
    tenant_concurrency: int
    subject_rpm: int
    tenant_rpm: int
    expected_rejection_codes: tuple[str, ...] = ()
    require_expected_rejection: bool = False
    expect_all_admitted: bool = False

    def validate(self) -> None:
        """在运行前拒绝无界或自相矛盾的 Load 参数。"""

        if not 1 <= self.attempts <= 100_000:
            raise ValueError("attempts must stay within [1, 100000]")
        if not 1 <= self.workers <= 512:
            raise ValueError("workers must stay within [1, 512]")
        if not 0 <= self.hold_ms <= 60_000:
            raise ValueError("hold_ms must stay within [0, 60000]")
        if not 1 <= self.tenant_count <= 10_000:
            raise ValueError("tenant_count must stay within [1, 10000]")
        if not 1 <= self.subject_count <= 100_000:
            raise ValueError("subject_count must stay within [1, 100000]")
        if not 1 <= self.tenant_concurrency <= self.global_concurrency <= 10_000:
            raise ValueError("tenant_concurrency must be <= global_concurrency")


@dataclass(frozen=True)
class LatencySummary:
    count: int
    p50: float | None
    p95: float | None
    p99: float | None
    maximum: float | None


def percentile(values: Iterable[float], q: float) -> float | None:
    """使用 Nearest-Rank 计算 Percentile，避免额外统计依赖。"""

    numbers = sorted(float(value) for value in values)
    if not numbers:
        return None
    if not 0 <= q <= 1:
        raise ValueError("percentile q must stay within [0, 1]")
    if q == 0:
        return numbers[0]

    import math

    index = max(0, min(len(numbers) - 1, math.ceil(q * len(numbers)) - 1))
    return numbers[index]


def _latency_summary(values: list[float]) -> LatencySummary:
    if not values:
        return LatencySummary(0, None, None, None, None)
    return LatencySummary(
        count=len(values),
        p50=round(percentile(values, 0.50), 3),
        p95=round(percentile(values, 0.95), 3),
        p99=round(percentile(values, 0.99), 3),
        maximum=round(max(values), 3),
    )


def build_scenarios(profile: str) -> tuple[LoadScenario, ...]:
    """返回 CI Smoke 或 Lab Profile。"""

    normalized = profile.strip().lower()
    if normalized == "ci-smoke":
        return (
            LoadScenario(
                "baseline", 320, 16, 1, 8, 64,
                64, 16, 100_000, 100_000,
                expect_all_admitted=True,
            ),
            LoadScenario(
                "tenant-saturation", 120, 24, 20, 1, 24,
                64, 4, 100_000, 100_000,
                expected_rejection_codes=("TENANT_CONCURRENCY_LIMIT",),
                require_expected_rejection=True,
            ),
            LoadScenario(
                "global-saturation", 160, 32, 20, 32, 32,
                8, 8, 100_000, 100_000,
                expected_rejection_codes=("GLOBAL_CONCURRENCY_LIMIT",),
                require_expected_rejection=True,
            ),
            LoadScenario(
                "subject-rate", 5, 1, 0, 1, 1,
                8, 8, 3, 100,
                expected_rejection_codes=("SUBJECT_RATE_LIMITED",),
                require_expected_rejection=True,
            ),
        )

    if normalized == "lab":
        return (
            LoadScenario(
                "baseline", 5_000, 64, 1, 16, 256,
                256, 64, 1_000_000, 1_000_000,
                expect_all_admitted=True,
            ),
            LoadScenario(
                "tenant-saturation", 1_000, 64, 20, 1, 64,
                128, 8, 1_000_000, 1_000_000,
                expected_rejection_codes=("TENANT_CONCURRENCY_LIMIT",),
                require_expected_rejection=True,
            ),
            LoadScenario(
                "global-saturation", 1_000, 64, 20, 64, 64,
                16, 16, 1_000_000, 1_000_000,
                expected_rejection_codes=("GLOBAL_CONCURRENCY_LIMIT",),
                require_expected_rejection=True,
            ),
            LoadScenario(
                "subject-rate", 20, 1, 0, 1, 1,
                16, 16, 10, 100,
                expected_rejection_codes=("SUBJECT_RATE_LIMITED",),
                require_expected_rejection=True,
            ),
        )

    raise ValueError(f"Unknown load profile: {profile}")


def _synthetic_context(*, tenant_id: str, subject: str) -> RequestContext:
    """构造不含真实用户数据的 Synthetic RequestContext。"""

    return RequestContext(
        tenant_id=tenant_id,
        subject=subject,
        scopes=frozenset({"commerce:semantic:read"}),
        allowed_metrics=frozenset({"gross_sales"}),
    )


def _configure_scenario_env(
    *,
    redis_url: str,
    namespace: str,
    scenario: LoadScenario,
) -> None:
    """把 Scenario 限制写入当前 Benchmark Process 的 Env。"""

    os.environ["AGENT_API_TRAFFIC_BACKEND"] = "redis"
    os.environ["AGENT_API_REDIS_URL"] = redis_url
    os.environ["AGENT_API_REDIS_NAMESPACE"] = namespace
    os.environ["AGENT_API_REDIS_OPERATION_TIMEOUT_SECONDS"] = "1"
    os.environ["AGENT_API_REDIS_LEASE_TTL_SECONDS"] = "30"
    os.environ["AGENT_API_REDIS_HEARTBEAT_SECONDS"] = "5"
    os.environ["AGENT_API_GLOBAL_CONCURRENCY"] = str(scenario.global_concurrency)
    os.environ["AGENT_API_TENANT_CONCURRENCY"] = str(scenario.tenant_concurrency)
    os.environ["AGENT_API_SUBJECT_RPM"] = str(scenario.subject_rpm)
    os.environ["AGENT_API_TENANT_RPM"] = str(scenario.tenant_rpm)


async def _close_guards(guards: Iterable[RedisTrafficGuard]) -> None:
    for guard in guards:
        try:
            await guard.backend.client.aclose()
        except Exception:
            pass


async def _run_scenario(
    project_root: Path,
    *,
    redis_url: str,
    namespace_prefix: str,
    scenario: LoadScenario,
) -> dict:
    """对真实 Redis 运行一个有界 Scenario，并返回聚合 Evidence。"""

    scenario.validate()
    namespace = f"{namespace_prefix}:{scenario.name}:{uuid4().hex[:10]}"
    if len(namespace) > 120:
        raise ValueError("Generated Redis namespace exceeds governed maximum 120 chars.")

    _configure_scenario_env(
        redis_url=redis_url,
        namespace=namespace,
        scenario=scenario,
    )

    guard_count = min(scenario.workers, 16)
    guards = tuple(
        RedisTrafficGuard.from_env(project_root)
        for _ in range(guard_count)
    )

    admission_latencies_ms: list[float] = []
    release_latencies_ms: list[float] = []
    rejection_counts: Counter[str] = Counter()
    unexpected_errors: list[str] = []

    admitted = 0
    active = 0
    peak_in_flight = 0
    state_lock = asyncio.Lock()

    async def worker(worker_id: int) -> None:
        nonlocal admitted, active, peak_in_flight
        guard = guards[worker_id % guard_count]

        for attempt_index in range(worker_id, scenario.attempts, scenario.workers):
            context = _synthetic_context(
                tenant_id=f"load-tenant-{attempt_index % scenario.tenant_count}",
                subject=f"load-subject-{attempt_index % scenario.subject_count}",
            )

            started = perf_counter()
            try:
                lease = await guard.acquire(context)
                admission_latencies_ms.append((perf_counter() - started) * 1000)
            except AdmissionRejected as rejected:
                admission_latencies_ms.append((perf_counter() - started) * 1000)
                rejection_counts[rejected.code] += 1
                continue
            except TrafficGuardUnavailable:
                unexpected_errors.append("TRAFFIC_GUARD_UNAVAILABLE")
                continue
            except Exception:
                unexpected_errors.append("UNEXPECTED_ADMISSION_EXCEPTION")
                continue

            async with state_lock:
                admitted += 1
                active += 1
                peak_in_flight = max(peak_in_flight, active)

            try:
                if scenario.hold_ms > 0:
                    await asyncio.sleep(scenario.hold_ms / 1000)
            finally:
                release_started = perf_counter()
                try:
                    await lease.release_async()
                except TrafficGuardUnavailable:
                    unexpected_errors.append("TRAFFIC_LEASE_RELEASE_UNAVAILABLE")
                except Exception:
                    unexpected_errors.append("UNEXPECTED_RELEASE_EXCEPTION")
                finally:
                    release_latencies_ms.append((perf_counter() - release_started) * 1000)
                    async with state_lock:
                        active = max(0, active - 1)

    started = perf_counter()
    try:
        await asyncio.gather(*(worker(worker_id) for worker_id in range(scenario.workers)))
    finally:
        duration_seconds = max(0.000001, perf_counter() - started)
        await _close_guards(guards)

    total_rejected = sum(rejection_counts.values())
    expected_codes = set(scenario.expected_rejection_codes)
    unexpected_rejection_count = sum(
        count
        for code, count in rejection_counts.items()
        if code not in expected_codes
    )
    expected_rejection_seen = (
        True
        if not scenario.require_expected_rejection
        else any(rejection_counts.get(code, 0) > 0 for code in expected_codes)
    )
    all_admitted_ok = (
        True
        if not scenario.expect_all_admitted
        else admitted == scenario.attempts and total_rejected == 0
    )
    correctness_pass = bool(
        not unexpected_errors
        and unexpected_rejection_count == 0
        and expected_rejection_seen
        and all_admitted_ok
        and admitted + total_rejected == scenario.attempts
    )

    return {
        "scenario": {
            **asdict(scenario),
            "expected_rejection_codes": list(scenario.expected_rejection_codes),
        },
        "result": {
            "attempts": scenario.attempts,
            "admitted": admitted,
            "rejected": total_rejected,
            "rejection_counts": dict(sorted(rejection_counts.items())),
            "unexpected_rejection_count": unexpected_rejection_count,
            "unexpected_error_count": len(unexpected_errors),
            "unexpected_error_codes": sorted(Counter(unexpected_errors).keys()),
            "peak_in_flight": peak_in_flight,
            "duration_seconds": round(duration_seconds, 4),
            "attempts_per_second": round(scenario.attempts / duration_seconds, 2),
            "admitted_per_second": round(admitted / duration_seconds, 2),
            "correctness_pass": correctness_pass,
        },
        "latency_ms": {
            "admission": asdict(_latency_summary(admission_latencies_ms)),
            "release": asdict(_latency_summary(release_latencies_ms)),
        },
    }


def _guardrail_defaults(project_root: Path) -> dict[str, int | float]:
    policy = yaml.safe_load(
        (project_root / "agent/contracts/agent_runtime_slo_policy.yml").read_text(
            encoding="utf-8"
        )
    )
    return {
        name: config["default"]
        for name, config in policy["limits"].items()
        if isinstance(config, dict) and "default" in config
    }


async def run_profile(
    project_root: Path | str,
    *,
    profile: str,
    output_path: Path | str,
    environment_label: str,
) -> dict:
    """运行 Profile 并写入不含 Secret 的 JSON Load Evidence。"""

    root = Path(project_root).resolve()
    output = Path(output_path)

    redis_url = os.getenv("AGENT_API_REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError("AGENT_API_REDIS_URL is required for Redis load evidence.")

    base_namespace = os.getenv(
        "AGENT_API_REDIS_NAMESPACE",
        "commerce:agent:load",
    ).strip()
    namespace_prefix = f"{base_namespace}:load:{profile}"

    scenario_results = []
    for scenario in build_scenarios(profile):
        scenario_results.append(
            await _run_scenario(
                root,
                redis_url=redis_url,
                namespace_prefix=namespace_prefix,
                scenario=scenario,
            )
        )

    all_correct = all(
        item["result"]["correctness_pass"]
        for item in scenario_results
    )

    report = {
        "schema_version": 1,
        "evidence_kind": "REDIS_ADMISSION_LOAD_OBSERVATION",
        "calibration_status": "LAB_OBSERVED_NOT_PROMOTED",
        "production_slo_authority": False,
        "guardrails_promoted": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "environment": {
            "label": environment_label,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
            "git_sha": os.getenv("GITHUB_SHA", ""),
            "redis_endpoint_recorded": False,
        },
        "scope": {
            "measures": "redis_admission_layer",
            "includes_real_redis": True,
            "includes_fastapi_http": False,
            "includes_llm": False,
            "includes_metricflow": False,
            "includes_external_tools": False,
        },
        "initial_guardrail_defaults": _guardrail_defaults(root),
        "scenario_results": scenario_results,
        "correctness_pass": all_correct,
        "promotion": {
            "production_slo_status": "UNCALIBRATED",
            "automatic_promotion": False,
            "reason": (
                "Lab/CI admission-layer observations are not representative "
                "end-to-end production SLO evidence."
            ),
        },
    }

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if redis_url in serialized:
        raise RuntimeError("Redis URL must never appear in load evidence.")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
