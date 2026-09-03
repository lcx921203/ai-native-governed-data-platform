"""Representative Staging Calibration Runner V1（代表性预生产校准执行器）。

Runner 消费已经通过验证的 Representative Staging Manifest V1，并真正调用已部署的
Agent API。它使用多租户身份、混合逻辑工作负载、Live LLM 与真实受治理工具链，随后
从内部 Audit JSONL 对齐 Runtime Intent、身份、LLM/Tool 使用以及 Audit Group Commit
持久化回执。

最终 Evidence 只保留聚合指标，不保存 Prompt、Answer、Bearer Token、Endpoint、
Audit Path、Raw Trace ID 或 Raw Group Commit Batch ID。
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from .staging_evidence_manifest import validate_staging_evidence_file


POLICY_PATH = "agent/contracts/agent_representative_staging_calibration_policy.yml"
EVIDENCE_KIND = "REPRESENTATIVE_STAGING_CALIBRATION_RUN_V1"
PLAN_KIND = "REPRESENTATIVE_STAGING_CALIBRATION_PLAN_V1"


@dataclass(frozen=True)
class HTTPResult:
    """一次 HTTP 请求的进程内结果；trace_id 只用于 Audit 对齐，不进入 Evidence。"""

    status: int
    payload: Mapping[str, Any]
    elapsed_ms: float
    trace_id: str


@dataclass(frozen=True)
class WorkloadRequest:
    """一次计划内请求；question 与身份明细只在进程内存在。"""

    logical_intent: str
    case_id: str
    question: str
    identity_id: str
    tenant_id: str
    subject: str
    token: str
    expected_runtime_intents: tuple[str, ...]


def load_representative_staging_calibration_policy(
    project_root: Path | str,
) -> dict:
    """读取独立版本化 Runner 策略。"""

    root = Path(project_root).resolve()
    return yaml.safe_load((root / POLICY_PATH).read_text(encoding="utf-8"))


def _mapping(value: Any) -> Mapping:
    """把非 Mapping 收敛为空 Mapping，便于生成确定性校验错误。"""

    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    """只接受非布尔正整数。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _positive_float(value: Any) -> float | None:
    """只接受有限正数；布尔值不能被当作 0/1。"""

    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result <= 0.0 or result == float("inf") or result != result:
        return None
    return result


def _collect_forbidden_keys(value: Any, forbidden: set[str]) -> list[str]:
    """递归扫描运行计划键名，防止把 Token/Credential 直接写进 YAML。"""

    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in forbidden:
                found.append(normalized)
            found.extend(_collect_forbidden_keys(child, forbidden))
    elif isinstance(value, list):
        for child in value:
            found.extend(_collect_forbidden_keys(child, forbidden))
    return found


def _contains_unresolved_placeholder(value: Any, prefix: str) -> bool:
    """模板中的 ${...} 必须在真实计划里被替换，避免误跑占位配置。"""

    if isinstance(value, str):
        return prefix in value
    if isinstance(value, Mapping):
        return any(
            _contains_unresolved_placeholder(child, prefix)
            for child in value.values()
        )
    if isinstance(value, list):
        return any(
            _contains_unresolved_placeholder(child, prefix)
            for child in value
        )
    return False


def validate_representative_staging_plan(
    plan: Mapping,
    *,
    manifest_validation: Mapping,
    policy: Mapping,
    environment: Mapping[str, str] | None = None,
) -> dict:
    """校验私有运行计划；只返回有界错误代码，不返回 Prompt 或 Token。"""

    errors: list[str] = []
    runner_policy = _mapping(policy.get("runner"))
    workload_policy = _mapping(policy.get("workload"))
    probe_policy = _mapping(policy.get("probes"))
    privacy_policy = _mapping(policy.get("privacy"))
    env = os.environ if environment is None else environment

    if manifest_validation.get("valid") is not True:
        errors.append("STAGING_MANIFEST_INVALID")
    if plan.get("schema_version") != int(runner_policy.get("plan_schema_version", 0)):
        errors.append("PLAN_SCHEMA_VERSION_MISMATCH")
    if plan.get("plan_kind") != runner_policy.get("plan_kind"):
        errors.append("PLAN_KIND_MISMATCH")

    forbidden = {
        str(value).strip().lower()
        for value in privacy_policy.get("forbidden_plan_keys", ())
    }
    if _collect_forbidden_keys(plan, forbidden):
        errors.append("FORBIDDEN_SECRET_FIELD_PRESENT")
    placeholder_prefix = str(
        privacy_policy.get("unresolved_placeholder_prefix") or "${"
    )
    if _contains_unresolved_placeholder(plan, placeholder_prefix):
        errors.append("UNRESOLVED_PLAN_PLACEHOLDER")

    endpoint = _mapping(plan.get("endpoint"))
    base_url_env = str(endpoint.get("base_url_env") or "").strip()
    audit_path_env = str(endpoint.get("audit_path_env") or "").strip()
    base_url = str(env.get(base_url_env, "")).strip() if base_url_env else ""
    audit_path = str(env.get(audit_path_env, "")).strip() if audit_path_env else ""
    if not base_url_env or not base_url:
        errors.append("STAGING_BASE_URL_ENV_MISSING")
    if not audit_path_env or not audit_path:
        errors.append("STAGING_AUDIT_PATH_ENV_MISSING")
    if base_url:
        parsed = urllib.parse.urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            errors.append("STAGING_BASE_URL_INVALID")
        elif bool(runner_policy.get("require_https", True)) and parsed.scheme != "https":
            errors.append("STAGING_HTTPS_REQUIRED")

    workers = _positive_int(endpoint.get("workers"))
    if workers is None or not (
        int(runner_policy.get("minimum_workers", 1))
        <= workers
        <= int(runner_policy.get("maximum_workers", 32))
    ):
        errors.append("WORKER_COUNT_OUTSIDE_GOVERNED_BOUNDS")
    timeout_seconds = _positive_float(endpoint.get("request_timeout_seconds"))
    if timeout_seconds is None or not (
        float(runner_policy.get("minimum_request_timeout_seconds", 1.0))
        <= timeout_seconds
        <= float(runner_policy.get("maximum_request_timeout_seconds", 120.0))
    ):
        errors.append("REQUEST_TIMEOUT_OUTSIDE_GOVERNED_BOUNDS")

    identities = plan.get("identities")
    if not isinstance(identities, list) or not identities:
        identities = []
        errors.append("IDENTITIES_MISSING")
    identity_ids: set[str] = set()
    tenant_ids: set[str] = set()
    subjects: set[str] = set()
    token_envs: set[str] = set()
    for item in identities:
        identity = _mapping(item)
        identity_id = str(identity.get("id") or "").strip()
        tenant_id = str(identity.get("tenant_id") or "").strip()
        subject = str(identity.get("subject") or "").strip()
        token_env = str(identity.get("bearer_token_env") or "").strip()
        if not identity_id or identity_id in identity_ids:
            errors.append("IDENTITY_ID_INVALID_OR_DUPLICATE")
        if not tenant_id or not subject:
            errors.append("IDENTITY_TENANT_OR_SUBJECT_MISSING")
        if not token_env or token_env in token_envs or not str(env.get(token_env, "")).strip():
            errors.append("IDENTITY_TOKEN_ENV_MISSING_OR_DUPLICATE")
        identity_ids.add(identity_id)
        tenant_ids.add(tenant_id)
        subjects.add(subject)
        token_envs.add(token_env)

    required_tenants = int(manifest_validation.get("tenant_count") or 0)
    required_subjects = int(manifest_validation.get("subject_count") or 0)
    if len(tenant_ids) < required_tenants:
        errors.append("PLAN_TENANT_CARDINALITY_TOO_LOW")
    if len(subjects) < required_subjects:
        errors.append("PLAN_SUBJECT_CARDINALITY_TOO_LOW")

    workloads = _mapping(plan.get("workloads"))
    mapping = _mapping(workload_policy.get("logical_intent_runtime_mapping"))
    for logical_intent in mapping:
        cases = workloads.get(logical_intent)
        if not isinstance(cases, list) or not cases:
            errors.append("WORKLOAD_CASE_MISSING")
            continue
        for case in cases:
            case_map = _mapping(case)
            if not str(case_map.get("id") or "").strip():
                errors.append("WORKLOAD_CASE_ID_MISSING")
            if not str(case_map.get("question") or "").strip():
                errors.append("WORKLOAD_QUESTION_MISSING")

    probes = _mapping(plan.get("probes"))
    for probe_name in ("timeout", "admission_saturation", "cross_tenant_isolation"):
        probe = _mapping(probes.get(probe_name))
        if not probe:
            errors.append("REQUIRED_PROBE_MISSING")
            continue
        if str(probe.get("identity") or "").strip() not in identity_ids:
            errors.append("PROBE_IDENTITY_UNKNOWN")
        if not str(probe.get("question") or "").strip():
            errors.append("PROBE_QUESTION_MISSING")

    saturation = _mapping(probes.get("admission_saturation"))
    burst = _positive_int(saturation.get("concurrent_requests"))
    saturation_policy = _mapping(probe_policy.get("admission_saturation"))
    if burst is None or not (
        int(saturation_policy.get("minimum_burst_requests", 2))
        <= burst
        <= int(saturation_policy.get("maximum_burst_requests", 128))
    ):
        errors.append("ADMISSION_BURST_OUTSIDE_GOVERNED_BOUNDS")

    cross_tenant = _mapping(probes.get("cross_tenant_isolation"))
    target_tenant = str(cross_tenant.get("target_tenant_id") or "").strip()
    if not target_tenant:
        errors.append("CROSS_TENANT_TARGET_MISSING")
    elif target_tenant not in tenant_ids:
        errors.append("CROSS_TENANT_TARGET_UNKNOWN")
    else:
        source_id = str(cross_tenant.get("identity") or "").strip()
        source_tenant = next(
            (
                str(_mapping(item).get("tenant_id") or "").strip()
                for item in identities
                if str(_mapping(item).get("id") or "").strip() == source_id
            ),
            "",
        )
        if source_tenant and source_tenant == target_tenant:
            errors.append("CROSS_TENANT_TARGET_MUST_DIFFER")

    return {
        "schema_version": 1,
        "validation_kind": "REPRESENTATIVE_STAGING_CALIBRATION_PLAN_VALIDATION_V1",
        "valid": not errors,
        "errors": sorted(set(errors)),
        "identity_count": len(identity_ids),
        "tenant_count": len(tenant_ids),
        "subject_count": len(subjects),
        "workers": workers,
        "request_timeout_seconds": timeout_seconds,
    }


def _percentile(values: list[float], p: float) -> float:
    """使用线性插值计算有限样本百分位。"""

    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * float(p)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * fraction,
        6,
    )


def _latency(values: list[float]) -> dict:
    """输出与现有 SLO Evidence 一致的有限延迟聚合。"""

    return {
        "count": len(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": round(max(values), 6) if values else 0.0,
    }


def _error_code(payload: Mapping[str, Any]) -> str:
    """从 FastAPI 统一错误投影中读取有界 code。"""

    detail = payload.get("detail")
    if isinstance(detail, Mapping):
        return str(detail.get("code") or "")
    return ""


def _http_post_agent_query(
    *,
    base_url: str,
    endpoint_path: str,
    token: str,
    question: str,
    timeout_seconds: float,
) -> HTTPResult:
    """调用已部署 Agent API；Token/Question 只存在于请求内存。"""

    url = base_url.rstrip("/") + endpoint_path
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        ) as response:
            raw = response.read()
            status = int(response.status)
            headers = response.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
        headers = exc.headers or {}
    elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    trace_id = str(payload.get("trace_id") or "").strip()
    if not trace_id and isinstance(payload.get("detail"), Mapping):
        trace_id = str(payload["detail"].get("trace_id") or "").strip()
    if not trace_id:
        trace_id = str(headers.get("X-Trace-Id") or "").strip()
    return HTTPResult(
        status=status,
        payload=payload,
        elapsed_ms=elapsed_ms,
        trace_id=trace_id,
    )


def _read_audit_rows(
    audit_path: Path,
    trace_ids: set[str],
    *,
    since: str,
) -> dict[str, dict[str, Mapping[str, Any]]]:
    """单次扫描共享 Audit JSONL，只返回当前 Runner 进程内 Trace 对应的内部行。"""

    rows: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    if not audit_path.exists():
        return rows
    with audit_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid audit JSONL at line {line_number}."
                ) from exc
            trace_id = str(row.get("trace_id") or "")
            if trace_id not in trace_ids:
                continue
            if since and str(row.get("occurred_at") or "") < since:
                continue
            event_type = str(row.get("event_type") or "RUNTIME")
            rows[trace_id][event_type] = row
    return rows


def _numeric_metrics(row: Mapping[str, Any]) -> dict[str, float]:
    """读取 Audit Numeric Metrics；自由文本不会被拷贝到 Evidence。"""

    output: dict[str, float] = {}
    for item in row.get("numeric_metrics") or ():
        metric = str(_mapping(item).get("metric") or "").strip()
        if not metric:
            continue
        try:
            output[metric] = max(0.0, float(_mapping(item).get("value") or 0.0))
        except (TypeError, ValueError):
            continue
    return output


def _stage_timings(row: Mapping[str, Any]) -> dict[str, float]:
    """读取 Audit Stage Timings，用于聚合 Durability Wait。"""

    output: dict[str, float] = {}
    for item in row.get("stage_timings") or ():
        stage = str(_mapping(item).get("stage") or "").strip()
        if not stage:
            continue
        try:
            output[stage] = max(0.0, float(_mapping(item).get("duration_ms") or 0.0))
        except (TypeError, ValueError):
            continue
    return output


def _build_workload_requests(
    plan: Mapping,
    *,
    manifest: Mapping,
    policy: Mapping,
    environment: Mapping[str, str],
) -> list[WorkloadRequest]:
    """严格按 Manifest 的逻辑工作负载次数生成请求，并在身份之间轮转。"""

    identities = [dict(_mapping(item)) for item in plan.get("identities") or ()]
    identity_by_id = {
        str(item["id"]): item
        for item in identities
    }
    identity_cycle = list(identity_by_id.values())
    workloads = _mapping(plan.get("workloads"))
    manifest_counts = _mapping(
        _mapping(manifest.get("workload")).get("intent_request_counts")
    )
    runtime_mapping = _mapping(
        _mapping(policy.get("workload")).get("logical_intent_runtime_mapping")
    )
    requests: list[WorkloadRequest] = []
    identity_index = 0
    for logical_intent, raw_count in manifest_counts.items():
        count = int(raw_count)
        cases = [dict(_mapping(item)) for item in workloads.get(logical_intent) or ()]
        expected = tuple(str(value) for value in runtime_mapping.get(logical_intent) or ())
        if not cases or not expected:
            raise ValueError(
                f"Manifest workload {logical_intent!r} has no governed runner mapping/case."
            )
        for index in range(count):
            case = cases[index % len(cases)]
            identity = identity_cycle[identity_index % len(identity_cycle)]
            identity_index += 1
            token_env = str(identity["bearer_token_env"])
            requests.append(
                WorkloadRequest(
                    logical_intent=str(logical_intent),
                    case_id=str(case["id"]),
                    question=str(case["question"]),
                    identity_id=str(identity["id"]),
                    tenant_id=str(identity["tenant_id"]),
                    subject=str(identity["subject"]),
                    token=str(environment[token_env]),
                    expected_runtime_intents=expected,
                )
            )
    return requests


def _run_probe(
    name: str,
    probe: Mapping,
    *,
    identity_by_id: Mapping[str, Mapping[str, Any]],
    environment: Mapping[str, str],
    base_url: str,
    endpoint_path: str,
    timeout_seconds: float,
    policy: Mapping,
    transport: Callable[..., HTTPResult],
) -> dict:
    """执行 Timeout / Cross-tenant Probe；Admission Burst 由专用分支处理。"""

    identity = identity_by_id[str(probe["identity"])]
    result = transport(
        base_url=base_url,
        endpoint_path=endpoint_path,
        token=str(environment[str(identity["bearer_token_env"])]),
        question=str(probe["question"]),
        timeout_seconds=timeout_seconds,
    )
    expected = _mapping(_mapping(policy.get("probes")).get(name))
    return {
        "passed": (
            result.status == int(expected.get("expected_http_status", -1))
            and _error_code(result.payload) == str(expected.get("expected_error_code") or "")
        ),
        "http_status": result.status,
        "error_code": _error_code(result.payload) or None,
        "latency_ms": round(result.elapsed_ms, 6),
    }


def _run_admission_probe(
    probe: Mapping,
    *,
    identity_by_id: Mapping[str, Mapping[str, Any]],
    environment: Mapping[str, str],
    base_url: str,
    endpoint_path: str,
    timeout_seconds: float,
    policy: Mapping,
    transport: Callable[..., HTTPResult],
) -> dict:
    """并发触发共享 Admission；至少观察到一个受治理 429 才通过。"""

    identity = identity_by_id[str(probe["identity"])]
    token = str(environment[str(identity["bearer_token_env"])] )
    count = int(probe["concurrent_requests"])

    def invoke(_index: int) -> HTTPResult:
        return transport(
            base_url=base_url,
            endpoint_path=endpoint_path,
            token=token,
            question=str(probe["question"]),
            timeout_seconds=timeout_seconds,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as executor:
        results = list(executor.map(invoke, range(count)))

    status_counts = Counter(result.status for result in results)
    error_counts = Counter(
        _error_code(result.payload)
        for result in results
        if result.status == 429
    )
    probe_policy = _mapping(_mapping(policy.get("probes")).get("admission_saturation"))
    allowed_codes = {
        str(value) for value in probe_policy.get("allowed_error_codes", ())
    }
    expected_status = int(probe_policy.get("expected_http_status", 429))
    expected_rejections = sum(
        count_value
        for code, count_value in error_counts.items()
        if code in allowed_codes
    )
    unexpected_status = sum(
        count_value
        for status, count_value in status_counts.items()
        if status not in {200, expected_status}
    )
    return {
        "passed": expected_rejections > 0 and unexpected_status == 0,
        "request_count": count,
        "http_status_counts": {
            str(key): value for key, value in sorted(status_counts.items())
        },
        "governed_rejection_count": expected_rejections,
        "unexpected_status_count": unexpected_status,
    }


def build_representative_staging_evidence(
    *,
    manifest_validation: Mapping,
    manifest: Mapping,
    plan_validation: Mapping,
    workload_requests: list[WorkloadRequest],
    workload_results: list[HTTPResult],
    audit_rows: Mapping[str, Mapping[str, Mapping[str, Any]]],
    probe_results: Mapping[str, Mapping[str, Any]],
    policy: Mapping,
    generated_at: str,
) -> dict:
    """把 HTTP + Audit 进程内明细收敛为去敏 Representative Staging Evidence。"""

    workload_policy = _mapping(policy.get("workload"))
    status_counts: Counter[int] = Counter()
    logical_counts: Counter[str] = Counter()
    runtime_intent_counts: Counter[str] = Counter()
    latencies: list[float] = []
    runtime_duration_ms: list[float] = []
    durability_wait_ms: list[float] = []
    batch_sync_ms_by_id: dict[int, float] = {}
    runtime_records_in_batch: list[float] = []
    unique_batch_ids: set[int] = set()
    runtime_audit_matches = 0
    persistence_receipt_matches = 0
    identity_matches = 0
    intent_matches = 0
    answer_validated_matches = 0
    llm_call_matches = 0
    tool_result_matches = 0
    response_contract_matches = 0
    total_llm_calls = 0
    total_llm_tokens = 0
    provider_cost_values: list[float] = []
    monetary_cost_known_count = 0

    for request, result in zip(workload_requests, workload_results):
        status_counts[result.status] += 1
        logical_counts[request.logical_intent] += 1
        latencies.append(result.elapsed_ms)
        response_contract_ok = (
            result.status == 200
            and result.payload.get("status") == "COMPLETE"
            and result.payload.get("answer_validated") is True
            and bool(result.trace_id)
        )
        if response_contract_ok:
            response_contract_matches += 1

        events = audit_rows.get(result.trace_id) or {}
        runtime_row = _mapping(events.get("RUNTIME"))
        api_timing_row = _mapping(events.get("API_TIMING"))
        if runtime_row:
            runtime_audit_matches += 1
            runtime_intent = str(runtime_row.get("intent") or "")
            runtime_intent_counts[runtime_intent] += 1
            if (
                str(runtime_row.get("tenant_id") or "") == request.tenant_id
                and str(runtime_row.get("subject") or "") == request.subject
            ):
                identity_matches += 1
            if runtime_intent in request.expected_runtime_intents:
                intent_matches += 1
            if runtime_row.get("answer_validated") is True:
                answer_validated_matches += 1
            llm_calls = int(runtime_row.get("llm_calls") or 0)
            tool_result_count = int(runtime_row.get("tool_result_count") or 0)
            total_llm_calls += max(0, llm_calls)
            total_llm_tokens += max(0, int(runtime_row.get("llm_total_tokens") or 0))
            if llm_calls > 0:
                llm_call_matches += 1
            if tool_result_count > 0:
                tool_result_matches += 1
            try:
                runtime_duration_ms.append(max(0.0, float(runtime_row.get("duration_ms") or 0.0)))
            except (TypeError, ValueError):
                pass
            if runtime_row.get("monetary_cost_known") is True:
                monetary_cost_known_count += 1
                raw_cost = runtime_row.get("provider_cost_usd")
                if raw_cost is not None:
                    try:
                        provider_cost_values.append(max(0.0, float(raw_cost)))
                    except (TypeError, ValueError):
                        pass

        if api_timing_row:
            metrics = _numeric_metrics(api_timing_row)
            stages = _stage_timings(api_timing_row)
            batch_id = int(metrics.get("runtime.audit.batch_id", 0.0))
            if batch_id > 0:
                persistence_receipt_matches += 1
                unique_batch_ids.add(batch_id)
                runtime_records_in_batch.append(
                    metrics.get("runtime.audit.batch_runtime_records", 0.0)
                )
                batch_sync_ms_by_id.setdefault(
                    batch_id,
                    metrics.get("runtime.audit.batch_sync_ms", 0.0),
                )
            if "runtime.audit.durability_wait" in stages:
                durability_wait_ms.append(stages["runtime.audit.durability_wait"])

    total = len(workload_requests)
    def coverage(matches: int) -> float:
        return 1.0 if total == 0 else round(matches / total, 6)

    runtime_audit_coverage = coverage(runtime_audit_matches)
    persistence_coverage = coverage(persistence_receipt_matches)
    identity_coverage = coverage(identity_matches)
    intent_coverage = coverage(intent_matches)
    validated_coverage = coverage(answer_validated_matches)
    llm_coverage = coverage(llm_call_matches)
    tool_coverage = coverage(tool_result_matches)
    response_contract_coverage = coverage(response_contract_matches)
    all_http_200 = status_counts.get(200, 0) == total

    grouped_fraction = (
        round(
            sum(1 for value in runtime_records_in_batch if value > 1.0)
            / len(runtime_records_in_batch),
            6,
        )
        if runtime_records_in_batch
        else 0.0
    )
    records_per_sync = (
        round(persistence_receipt_matches / len(unique_batch_ids), 6)
        if unique_batch_ids
        else 0.0
    )

    required_runtime_coverage = float(
        workload_policy.get("required_runtime_audit_coverage", 1.0)
    )
    required_persistence_coverage = float(
        workload_policy.get("required_audit_persistence_receipt_coverage", 1.0)
    )
    required_llm_coverage = float(
        workload_policy.get("required_live_llm_call_coverage", 1.0)
    )
    required_tool_coverage = float(
        workload_policy.get("required_tool_result_coverage", 1.0)
    )
    probes_pass = all(
        _mapping(probe_results.get(name)).get("passed") is True
        for name in ("timeout", "admission_saturation", "cross_tenant_isolation")
    )
    representative_pass = bool(
        manifest_validation.get("valid") is True
        and plan_validation.get("valid") is True
        and all_http_200
        and response_contract_coverage == 1.0
        and identity_coverage == 1.0
        and intent_coverage == 1.0
        and validated_coverage == 1.0
        and runtime_audit_coverage == required_runtime_coverage
        and persistence_coverage == required_persistence_coverage
        and llm_coverage == required_llm_coverage
        and tool_coverage == required_tool_coverage
        and probes_pass
    )

    environment = _mapping(manifest.get("environment"))
    return {
        "schema_version": 1,
        "evidence_kind": EVIDENCE_KIND,
        "calibration_status": (
            "REPRESENTATIVE_STAGING_PASS"
            if representative_pass
            else "REPRESENTATIVE_STAGING_FAILED"
        ),
        "production_slo_authority": False,
        "production_default_updated": False,
        "generated_at": generated_at,
        "environment": {
            "label": manifest_validation.get("environment_label"),
            "deployment_id": manifest_validation.get("deployment_id"),
            "git_sha": manifest_validation.get("git_sha"),
            "audit_group_commit_window_ms": manifest_validation.get(
                "audit_group_commit_window_ms"
            ),
            "endpoint_recorded": False,
            "audit_path_recorded": False,
        },
        "workload": {
            "request_count": total,
            "logical_intent_request_counts": dict(sorted(logical_counts.items())),
            "observed_runtime_intent_counts": dict(sorted(runtime_intent_counts.items())),
            "http_status_counts": {
                str(key): value for key, value in sorted(status_counts.items())
            },
            "http_latency_ms": _latency(latencies),
            "runtime_latency_ms": _latency(runtime_duration_ms),
            "response_contract_coverage": response_contract_coverage,
            "runtime_intent_match_coverage": intent_coverage,
            "tenant_subject_match_coverage": identity_coverage,
            "answer_validated_coverage": validated_coverage,
            "live_llm_call_coverage": llm_coverage,
            "tool_result_coverage": tool_coverage,
        },
        "audit": {
            "runtime_audit_coverage": runtime_audit_coverage,
            "persistence_receipt_coverage": persistence_coverage,
            "unique_sync_batches": len(unique_batch_ids),
            "runtime_records_per_sync": records_per_sync,
            "grouped_runtime_record_fraction": grouped_fraction,
            "durability_wait_latency_ms": _latency(durability_wait_ms),
            "batch_sync_latency_ms": _latency(list(batch_sync_ms_by_id.values())),
            "raw_trace_ids_recorded": False,
            "raw_group_commit_batch_ids_recorded": False,
        },
        "cost": {
            "llm_calls": total_llm_calls,
            "llm_total_tokens": total_llm_tokens,
            "monetary_cost_known_coverage": coverage(monetary_cost_known_count),
            "provider_cost_usd_sum": (
                round(sum(provider_cost_values), 8)
                if provider_cost_values
                else None
            ),
        },
        "tenancy": {
            "planned_tenant_count": int(plan_validation.get("tenant_count") or 0),
            "planned_subject_count": int(plan_validation.get("subject_count") or 0),
            "cross_tenant_isolation_probe_passed": bool(
                _mapping(probe_results.get("cross_tenant_isolation")).get("passed")
            ),
        },
        "probes": {
            "timeout": dict(_mapping(probe_results.get("timeout"))),
            "admission_saturation": dict(
                _mapping(probe_results.get("admission_saturation"))
            ),
            "cross_tenant_isolation": dict(
                _mapping(probe_results.get("cross_tenant_isolation"))
            ),
        },
        "privacy": {
            "prompts_recorded": False,
            "answers_recorded": False,
            "bearer_tokens_recorded": False,
            "endpoint_recorded": False,
            "audit_path_recorded": False,
        },
        "promotion": {
            "review_candidate_evidence": representative_pass,
            "automatic_production_promotion": False,
            "production_default_updated": False,
            "production_slo_authority": False,
            "explicit_human_approval_required": True,
            "next_consumer": "AUDIT_GROUP_COMMIT_WINDOW_PROMOTION_REVIEW",
        },
    }


def _assert_evidence_privacy(
    report: Mapping,
    *,
    forbidden_values: list[str],
    policy: Mapping,
) -> None:
    """最终序列化后再次做 Fail-Closed 隐私检查。"""

    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for value in forbidden_values:
        if value and value in serialized:
            raise RuntimeError(
                "Sensitive staging runtime value must not appear in evidence."
            )
    forbidden_keys = {
        str(value).strip().lower()
        for value in _mapping(policy.get("privacy")).get("evidence_forbids", ())
    }
    if _collect_forbidden_keys(report, forbidden_keys):
        raise RuntimeError("Representative staging evidence contains a forbidden field.")


def run_representative_staging_calibration(
    project_root: Path | str,
    *,
    manifest_path: Path | str,
    plan_path: Path | str,
    output_path: Path | str,
    transport: Callable[..., HTTPResult] | None = None,
    audit_loader: Callable[[Path, set[str]], Mapping[str, Mapping[str, Mapping[str, Any]]]] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict:
    """执行一次代表性 Staging 混合负载 + 三类 Probe，并写出去敏 Evidence。"""

    root = Path(project_root).resolve()
    env = os.environ if environment is None else environment
    policy = load_representative_staging_calibration_policy(root)
    manifest = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise TypeError("Representative staging manifest must be a mapping.")
    manifest_validation = validate_staging_evidence_file(
        root,
        manifest_path=manifest_path,
    )
    if manifest_validation.get("valid") is not True:
        raise ValueError(
            "Representative staging manifest failed validation: "
            + ",".join(manifest_validation.get("errors") or ())
        )

    plan = yaml.safe_load(Path(plan_path).read_text(encoding="utf-8"))
    if not isinstance(plan, Mapping):
        raise TypeError("Representative staging calibration plan must be a mapping.")
    plan_validation = validate_representative_staging_plan(
        plan,
        manifest_validation=manifest_validation,
        policy=policy,
        environment=env,
    )
    if plan_validation.get("valid") is not True:
        raise ValueError(
            "Representative staging calibration plan failed validation: "
            + ",".join(plan_validation.get("errors") or ())
        )

    endpoint = _mapping(plan.get("endpoint"))
    runner_policy = _mapping(policy.get("runner"))
    base_url = str(env[str(endpoint["base_url_env"])]).strip()
    audit_path = Path(str(env[str(endpoint["audit_path_env"])]).strip())
    workers = int(endpoint["workers"])
    timeout_seconds = float(endpoint["request_timeout_seconds"])
    endpoint_path = str(runner_policy.get("endpoint_path") or "/api/v1/agent/query")
    http_transport = transport or _http_post_agent_query

    workload_requests = _build_workload_requests(
        plan,
        manifest=manifest,
        policy=policy,
        environment=env,
    )
    started_at = datetime.now(timezone.utc).isoformat()

    def invoke(request: WorkloadRequest) -> HTTPResult:
        return http_transport(
            base_url=base_url,
            endpoint_path=endpoint_path,
            token=request.token,
            question=request.question,
            timeout_seconds=timeout_seconds,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        workload_results = list(executor.map(invoke, workload_requests))

    identities = {
        str(_mapping(item).get("id")): dict(_mapping(item))
        for item in plan.get("identities") or ()
    }
    probes = _mapping(plan.get("probes"))
    probe_results = {
        "timeout": _run_probe(
            "timeout",
            _mapping(probes.get("timeout")),
            identity_by_id=identities,
            environment=env,
            base_url=base_url,
            endpoint_path=endpoint_path,
            timeout_seconds=timeout_seconds,
            policy=policy,
            transport=http_transport,
        ),
        "admission_saturation": _run_admission_probe(
            _mapping(probes.get("admission_saturation")),
            identity_by_id=identities,
            environment=env,
            base_url=base_url,
            endpoint_path=endpoint_path,
            timeout_seconds=timeout_seconds,
            policy=policy,
            transport=http_transport,
        ),
        "cross_tenant_isolation": _run_probe(
            "cross_tenant_isolation",
            _mapping(probes.get("cross_tenant_isolation")),
            identity_by_id=identities,
            environment=env,
            base_url=base_url,
            endpoint_path=endpoint_path,
            timeout_seconds=timeout_seconds,
            policy=policy,
            transport=http_transport,
        ),
    }

    trace_ids = {
        result.trace_id
        for result in workload_results
        if result.trace_id
    }
    if audit_loader is None:
        audit_rows = _read_audit_rows(
            audit_path,
            trace_ids,
            since=started_at,
        )
    else:
        audit_rows = audit_loader(audit_path, trace_ids)

    report = build_representative_staging_evidence(
        manifest_validation=manifest_validation,
        manifest=manifest,
        plan_validation=plan_validation,
        workload_requests=workload_requests,
        workload_results=workload_results,
        audit_rows=audit_rows,
        probe_results=probe_results,
        policy=policy,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    forbidden_values = [base_url, str(audit_path)]
    for request in workload_requests:
        forbidden_values.extend([request.question, request.token])
    for probe in probes.values():
        question = str(_mapping(probe).get("question") or "")
        if question:
            forbidden_values.append(question)
    _assert_evidence_privacy(report, forbidden_values=forbidden_values, policy=policy)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
