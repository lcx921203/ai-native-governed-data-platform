"""Authenticated Agent API E2E Load（认证 Agent API 端到端负载）Harness。

V1 真实覆盖：
    Loopback TCP HTTP
      -> Bearer JWT
      -> JWKS Signature / Issuer / Audience / Exp Verification
      -> TrustedClaimsContextMapper
      -> RequestContext
      -> Redis Shared Admission
      -> GovernedAgentRuntime
      -> Deterministic Renderer
      -> Answer Validator
      -> Public API Response

为避免把实验室数据伪装成生产 SLO，V1 故意使用一个完全本地、可重复的
``METRIC_DEFINITION -> get_metric_context`` 路径：
- 使用仓库真实 Router / Context / Metadata Tool / Renderer / Validator；
- 不调用 Live OpenAI；
- 不调用真实 MetricFlow / Trino / DataHub Server；
- 不把 Prompt、Answer、Bearer Token、JWKS Private Key、Redis URL 写进 Evidence。

因此输出是 ``AUTHENTICATED_AGENT_API_E2E_LOAD_OBSERVATION``，
它比 Redis Admission-only Benchmark 更完整，但仍不是生产 SLO Authority。
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import platform
import socket
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import perf_counter
from typing import Iterable
from uuid import uuid4

import yaml

from .redis_load import percentile


QUESTION = "activity_net_sales 是什么意思？"
METRIC = "activity_net_sales"


@dataclass(frozen=True)
class APIE2EScenario:
    """一个受治理的 Agent API E2E Load Scenario。"""

    name: str
    attempts: int
    workers: int
    tenant_count: int
    subject_count: int
    global_concurrency: int
    tenant_concurrency: int
    subject_rpm: int
    tenant_rpm: int
    expect_all_200: bool = False
    expected_429_code: str | None = None
    require_expected_429: bool = False

    def validate(self) -> None:
        """限制 Benchmark 规模，避免脚本被误用成无界压测器。"""

        if not 1 <= self.attempts <= 20_000:
            raise ValueError("attempts must stay within [1, 20000]")
        if not 1 <= self.workers <= 128:
            raise ValueError("workers must stay within [1, 128]")
        if not 1 <= self.tenant_count <= 1_000:
            raise ValueError("tenant_count must stay within [1, 1000]")
        if not 1 <= self.subject_count <= 10_000:
            raise ValueError("subject_count must stay within [1, 10000]")
        if not 1 <= self.tenant_concurrency <= self.global_concurrency <= 1_000:
            raise ValueError("tenant_concurrency must be <= global_concurrency")


def build_api_e2e_scenarios(profile: str) -> tuple[APIE2EScenario, ...]:
    """返回 CI Smoke 或手动 Lab Profile。"""

    normalized = profile.strip().lower()

    if normalized == "ci-smoke":
        return (
            APIE2EScenario(
                name="authenticated-baseline",
                attempts=48,
                workers=8,
                tenant_count=4,
                subject_count=16,
                global_concurrency=32,
                tenant_concurrency=8,
                subject_rpm=100_000,
                tenant_rpm=100_000,
                expect_all_200=True,
            ),
            APIE2EScenario(
                name="tenant-concurrency",
                attempts=48,
                workers=16,
                tenant_count=1,
                subject_count=16,
                global_concurrency=32,
                tenant_concurrency=1,
                subject_rpm=100_000,
                tenant_rpm=100_000,
                expected_429_code="TENANT_CONCURRENCY_LIMIT",
                require_expected_429=True,
            ),
            APIE2EScenario(
                name="subject-rate",
                attempts=5,
                workers=1,
                tenant_count=1,
                subject_count=1,
                global_concurrency=8,
                tenant_concurrency=8,
                subject_rpm=3,
                tenant_rpm=100,
                expected_429_code="SUBJECT_RATE_LIMITED",
                require_expected_429=True,
            ),
        )

    if normalized == "lab":
        return (
            APIE2EScenario(
                name="authenticated-baseline",
                attempts=500,
                workers=32,
                tenant_count=8,
                subject_count=64,
                global_concurrency=128,
                tenant_concurrency=32,
                subject_rpm=1_000_000,
                tenant_rpm=1_000_000,
                expect_all_200=True,
            ),
            APIE2EScenario(
                name="tenant-concurrency",
                attempts=300,
                workers=48,
                tenant_count=1,
                subject_count=48,
                global_concurrency=128,
                tenant_concurrency=4,
                subject_rpm=1_000_000,
                tenant_rpm=1_000_000,
                expected_429_code="TENANT_CONCURRENCY_LIMIT",
                require_expected_429=True,
            ),
            APIE2EScenario(
                name="subject-rate",
                attempts=20,
                workers=1,
                tenant_count=1,
                subject_count=1,
                global_concurrency=16,
                tenant_concurrency=16,
                subject_rpm=10,
                tenant_rpm=100,
                expected_429_code="SUBJECT_RATE_LIMITED",
                require_expected_429=True,
            ),
        )

    raise ValueError(f"Unknown Agent API E2E profile: {profile}")


def _b64url_uint(value: int) -> str:
    """把 RSA Integer 转成 RFC 7517 使用的 Base64URLUInt。"""

    raw = value.to_bytes(
        max(1, (value.bit_length() + 7) // 8),
        byteorder="big",
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class LocalJWTAuthority:
    """仅用于本地/CI 的临时 RSA JWT Authority + JWKS HTTP Server。"""

    def __init__(self):
        """生成一次性 RSA Key；Private Key 只留在当前 Benchmark Process 内存。"""

        try:
            import jwt
            from cryptography.hazmat.primitives.asymmetric import rsa
        except ImportError as exc:
            raise RuntimeError(
                "Agent API E2E load requires PyJWT[crypto] from requirements-agent.txt."
            ) from exc

        self._jwt = jwt
        self._private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self.kid = f"e2e-{uuid4().hex[:12]}"
        self.issuer = "https://local-agent-e2e.invalid"
        self.audience = "commerce-agent-e2e"

        numbers = self._private_key.public_key().public_numbers()
        self.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self.kid,
                    "n": _b64url_uint(numbers.n),
                    "e": _b64url_uint(numbers.e),
                }
            ]
        }

        payload = json.dumps(
            self.jwks,
            separators=(",", ":"),
        ).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            """返回固定 JWKS；禁止输出访问日志。"""

            def do_GET(self):
                """只暴露当前临时 Authority 的 JWKS Endpoint。"""

                if self.path != "/.well-known/jwks.json":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "public, max-age=60")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, _format, *_args):
                """屏蔽 JWKS Server 的默认访问日志。"""

                return

        self._server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            Handler,
        )
        self._thread = Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()
        host, port = self._server.server_address
        self.jwks_url = f"http://{host}:{port}/.well-known/jwks.json"

    def token(self, *, tenant_id: str, subject: str) -> str:
        """签发只包含 Synthetic Identity 的短期 RS256 Bearer JWT。"""

        now = datetime.now(timezone.utc)
        claims = {
            "sub": subject,
            "client_id": "agent-e2e-load",
            "iss": self.issuer,
            "aud": self.audience,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "scope": "commerce:semantic:read",
            "tenant_id": tenant_id,
            "roles": ["load-test"],
            "allowed_metrics": [METRIC],
            "allowed_datasets": [],
            "allowed_entities": [],
            "allowed_dimensions": [],
            "allowed_knowledge_scopes": [],
            "dimension_scopes": {},
        }
        return self._jwt.encode(
            claims,
            self._private_key,
            algorithm="RS256",
            headers={"kid": self.kid},
        )

    def close(self) -> None:
        """停止临时 JWKS Server；Private Key 随 Process 生命周期销毁。"""

        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _free_port() -> int:
    """分配一个 Loopback TCP Port 给单 Worker Uvicorn。"""

    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    ) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(
    *,
    port: int,
    process: subprocess.Popen,
    timeout_seconds: float = 15.0,
) -> None:
    """等待真实 Uvicorn /health/ready 返回 200。"""

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Agent API subprocess exited before readiness.")
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                port,
                timeout=0.5,
            )
            connection.request("GET", "/health/ready")
            response = connection.getresponse()
            response.read()
            connection.close()
            if response.status == 200:
                return
        except OSError:
            pass
        time.sleep(0.1)

    raise RuntimeError("Agent API subprocess did not become ready within timeout.")


def _server_env(
    *,
    project_root: Path,
    redis_url: str,
    redis_namespace: str,
    authority: LocalJWTAuthority,
    scenario: APIE2EScenario,
) -> dict[str, str]:
    """构造每个 Scenario 独立的 Agent API Production-like Env。"""

    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(project_root),
            "AGENT_API_TRAFFIC_BACKEND": "redis",
            "AGENT_API_REDIS_URL": redis_url,
            "AGENT_API_REDIS_NAMESPACE": redis_namespace,
            "AGENT_API_REDIS_OPERATION_TIMEOUT_SECONDS": "1",
            "AGENT_API_REDIS_LEASE_TTL_SECONDS": "30",
            "AGENT_API_REDIS_HEARTBEAT_SECONDS": "5",
            "AGENT_API_GLOBAL_CONCURRENCY": str(scenario.global_concurrency),
            "AGENT_API_TENANT_CONCURRENCY": str(scenario.tenant_concurrency),
            "AGENT_API_SUBJECT_RPM": str(scenario.subject_rpm),
            "AGENT_API_TENANT_RPM": str(scenario.tenant_rpm),
            "AGENT_API_REQUEST_TIMEOUT_SECONDS": "10",
            "AGENT_API_JWKS_URL": authority.jwks_url,
            "AGENT_API_AUTH_ISSUER": authority.issuer,
            "AGENT_API_AUDIENCE": authority.audience,
            "AGENT_REQUIRE_REQUEST_CONTEXT": "true",
            "AGENT_RENDERER_MODE": "deterministic",
            "PHASE4G_ALLOW_OPENAI_CALL": "false",
            "AGENT_AUDIT_MODE": "disabled",
        }
    )

    # Defense-in-depth：即使 Runner 外部配置了 Provider Key，E2E CI 也不允许 Live LLM。
    env.pop("OPENAI_API_KEY", None)
    return env


def _start_agent_api(
    *,
    project_root: Path,
    port: int,
    env: dict[str, str],
) -> subprocess.Popen:
    """以真实 Uvicorn Single Worker 启动当前仓库 Agent API。"""

    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "agent.api.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        cwd=project_root,
        env=env,
    )


def _stop_agent_api(process: subprocess.Popen) -> None:
    """终止 Scenario Uvicorn；超时后再强制 Kill。"""

    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _request_once(
    connection: http.client.HTTPConnection,
    *,
    token: str,
) -> tuple[int, dict, float, bool]:
    """发送一次真实 HTTP POST，并只返回状态/结构化错误/耗时/响应契约结果。"""

    body = json.dumps(
        {"question": QUESTION},
        ensure_ascii=False,
    ).encode("utf-8")

    started = perf_counter()
    connection.request(
        "POST",
        "/api/v1/agent/query",
        body=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
    )
    response = connection.getresponse()
    raw = response.read()
    elapsed_ms = (perf_counter() - started) * 1000

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        payload = {}

    contract_ok = False
    if response.status == 200:
        trace_id = str(payload.get("trace_id") or "")
        contract_ok = bool(
            payload.get("answer_validated") is True
            and str(payload.get("answer") or "").strip()
            and trace_id
            and response.getheader("X-Trace-Id") == trace_id
        )
    elif response.status == 429:
        detail = payload.get("detail") or {}
        trace_id = str(detail.get("trace_id") or "")
        contract_ok = bool(
            detail.get("code")
            and trace_id
            and response.getheader("X-Trace-Id") == trace_id
            and response.getheader("Retry-After")
        )

    return response.status, payload, elapsed_ms, contract_ok


def _latency(values: list[float]) -> dict[str, float | int | None]:
    """把 HTTP Total Latency 收敛成有限 Percentile Evidence。"""

    if not values:
        return {
            "count": 0,
            "p50": None,
            "p95": None,
            "p99": None,
            "maximum": None,
        }
    return {
        "count": len(values),
        "p50": round(percentile(values, 0.50), 3),
        "p95": round(percentile(values, 0.95), 3),
        "p99": round(percentile(values, 0.99), 3),
        "maximum": round(max(values), 3),
    }


def _run_scenario(
    project_root: Path,
    *,
    redis_url: str,
    namespace_prefix: str,
    authority: LocalJWTAuthority,
    scenario: APIE2EScenario,
) -> dict:
    """运行一个真实 HTTP/JWT/Redis/Runtime Scenario。"""

    scenario.validate()

    namespace = (
        f"{namespace_prefix}:api-e2e:{scenario.name}:"
        f"{uuid4().hex[:8]}"
    )
    if len(namespace) > 120:
        raise ValueError("Generated Redis namespace exceeds governed maximum 120 chars.")

    identities = [
        (
            f"e2e-tenant-{index % scenario.tenant_count}",
            f"e2e-subject-{index % scenario.subject_count}",
        )
        for index in range(
            max(
                scenario.tenant_count,
                scenario.subject_count,
            )
        )
    ]
    tokens = {
        identity: authority.token(
            tenant_id=identity[0],
            subject=identity[1],
        )
        for identity in identities
    }

    port = _free_port()
    env = _server_env(
        project_root=project_root,
        redis_url=redis_url,
        redis_namespace=namespace,
        authority=authority,
        scenario=scenario,
    )
    process = _start_agent_api(
        project_root=project_root,
        port=port,
        env=env,
    )

    def sync_worker(worker_id: int) -> dict:
        """每个 Worker 独占一个 HTTP/1.1 Connection，顺序发送分片请求。"""

        status_counts: Counter[int] = Counter()
        code_counts: Counter[str] = Counter()
        latencies: list[float] = []
        contract_failures = 0
        unexpected_errors: Counter[str] = Counter()

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            port,
            timeout=15,
        )
        try:
            for attempt_index in range(
                worker_id,
                scenario.attempts,
                scenario.workers,
            ):
                identity = (
                    f"e2e-tenant-{attempt_index % scenario.tenant_count}",
                    f"e2e-subject-{attempt_index % scenario.subject_count}",
                )
                token = tokens[identity]
                try:
                    status, payload, elapsed_ms, contract_ok = _request_once(
                        connection,
                        token=token,
                    )
                except Exception:
                    unexpected_errors["HTTP_REQUEST_EXCEPTION"] += 1
                    connection.close()
                    connection = http.client.HTTPConnection(
                        "127.0.0.1",
                        port,
                        timeout=15,
                    )
                    continue

                status_counts[status] += 1
                latencies.append(elapsed_ms)
                if not contract_ok:
                    contract_failures += 1

                if status == 429:
                    detail = payload.get("detail") or {}
                    code_counts[str(detail.get("code") or "UNKNOWN_429")] += 1
                elif status != 200:
                    code_counts[f"HTTP_{status}"] += 1
        finally:
            connection.close()

        return {
            "status_counts": status_counts,
            "code_counts": code_counts,
            "latencies": latencies,
            "contract_failures": contract_failures,
            "unexpected_errors": unexpected_errors,
        }

    try:
        _wait_until_ready(
            port=port,
            process=process,
        )

        started = perf_counter()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=scenario.workers
        ) as executor:
            worker_results = list(
                executor.map(
                    sync_worker,
                    range(scenario.workers),
                )
            )
        duration_seconds = max(
            0.000001,
            perf_counter() - started,
        )
    finally:
        _stop_agent_api(process)

    status_counts: Counter[int] = Counter()
    code_counts: Counter[str] = Counter()
    unexpected_errors: Counter[str] = Counter()
    latencies: list[float] = []
    contract_failures = 0

    for item in worker_results:
        status_counts.update(item["status_counts"])
        code_counts.update(item["code_counts"])
        unexpected_errors.update(item["unexpected_errors"])
        latencies.extend(item["latencies"])
        contract_failures += int(item["contract_failures"])

    observed = sum(status_counts.values()) + sum(unexpected_errors.values())
    expected_429_seen = (
        True
        if not scenario.require_expected_429
        else code_counts.get(
            str(scenario.expected_429_code),
            0,
        )
        > 0
    )
    all_200_ok = (
        True
        if not scenario.expect_all_200
        else status_counts.get(200, 0) == scenario.attempts
    )
    unexpected_statuses = sum(
        count
        for status, count in status_counts.items()
        if status not in {200, 429}
    )
    unexpected_429 = sum(
        count
        for code, count in code_counts.items()
        if code.startswith("HTTP_")
        or (
            code.endswith("_LIMIT")
            or code.endswith("_LIMITED")
        )
        and scenario.expected_429_code
        and code != scenario.expected_429_code
    )

    correctness_pass = bool(
        observed == scenario.attempts
        and not unexpected_errors
        and contract_failures == 0
        and unexpected_statuses == 0
        and unexpected_429 == 0
        and expected_429_seen
        and all_200_ok
        and status_counts.get(200, 0) > 0
    )

    return {
        "scenario": asdict(scenario),
        "result": {
            "attempts": scenario.attempts,
            "status_counts": {
                str(key): value
                for key, value
                in sorted(status_counts.items())
            },
            "rejection_counts": dict(
                sorted(code_counts.items())
            ),
            "unexpected_error_count": sum(
                unexpected_errors.values()
            ),
            "unexpected_error_codes": dict(
                sorted(unexpected_errors.items())
            ),
            "response_contract_failure_count": contract_failures,
            "duration_seconds": round(duration_seconds, 4),
            "attempts_per_second": round(
                scenario.attempts / duration_seconds,
                2,
            ),
            "correctness_pass": correctness_pass,
        },
        "http_total_latency_ms": _latency(latencies),
    }


def _initial_guardrails(project_root: Path) -> dict:
    """读取当前 Initial Guardrail，只作为 Evidence Context。"""

    policy = yaml.safe_load(
        (
            project_root
            / "agent/contracts/agent_runtime_slo_policy.yml"
        ).read_text(encoding="utf-8")
    )
    return {
        key: value["default"]
        for key, value in policy["limits"].items()
        if isinstance(value, dict)
        and "default" in value
    }


def run_api_e2e_profile(
    project_root: Path | str,
    *,
    profile: str,
    output_path: Path | str,
    environment_label: str,
) -> dict:
    """运行真实 Authenticated Agent API Load，并写入隐私受控 JSON Evidence。"""

    root = Path(project_root).resolve()
    output = Path(output_path)
    redis_url = os.getenv(
        "AGENT_API_REDIS_URL",
        "",
    ).strip()
    if not redis_url:
        raise RuntimeError("AGENT_API_REDIS_URL is required for Agent API E2E load.")

    base_namespace = os.getenv(
        "AGENT_API_REDIS_NAMESPACE",
        "commerce:agent:e2e",
    ).strip()

    authority = LocalJWTAuthority()
    try:
        results = [
            _run_scenario(
                root,
                redis_url=redis_url,
                namespace_prefix=f"{base_namespace}:{profile}",
                authority=authority,
                scenario=scenario,
            )
            for scenario in build_api_e2e_scenarios(profile)
        ]
    finally:
        authority.close()

    report = {
        "schema_version": 1,
        "evidence_kind": "AUTHENTICATED_AGENT_API_E2E_LOAD_OBSERVATION",
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
            "jwks_endpoint_recorded": False,
        },
        "scope": {
            "includes_loopback_tcp_http": True,
            "includes_real_rs256_jwt_verification": True,
            "includes_local_ephemeral_jwks": True,
            "includes_trusted_request_context_mapping": True,
            "includes_real_redis_admission": True,
            "includes_governed_agent_runtime": True,
            "includes_metadata_context_tool": True,
            "includes_deterministic_renderer": True,
            "includes_answer_validator": True,
            "includes_live_llm": False,
            "includes_live_metricflow": False,
            "includes_live_trino": False,
            "includes_remote_datahub": False,
            "runtime_path": "METRIC_DEFINITION/get_metric_context",
        },
        "initial_guardrail_defaults": _initial_guardrails(root),
        "scenario_results": results,
        "correctness_pass": all(
            item["result"]["correctness_pass"]
            for item in results
        ),
        "promotion": {
            "production_slo_status": "UNCALIBRATED",
            "automatic_promotion": False,
            "reason": (
                "Loopback deterministic E2E observations still exclude representative "
                "production network, live LLM, MetricFlow, Trino, and external-tool latency."
            ),
        },
    }

    serialized = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
    )
    forbidden = [
        redis_url,
        authority.jwks_url,
        QUESTION,
    ]
    if any(value and value in serialized for value in forbidden):
        raise RuntimeError(
            "Secret/runtime endpoint/prompt must never appear in Agent API E2E evidence."
        )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    output.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return report
