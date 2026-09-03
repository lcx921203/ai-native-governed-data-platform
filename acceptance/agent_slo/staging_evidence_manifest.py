"""Representative Staging Evidence Manifest V1（代表性预生产证据清单）。

清单描述校准运行所在环境，而不是描述延迟结果本身。验证器只做机械门禁：
拓扑、依赖、请求组合、多租户探针和审计覆盖率全部满足后，证据才可进入人工评审。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

MANIFEST_KIND = "REPRESENTATIVE_STAGING_EVIDENCE_MANIFEST_V1"
_HEX_SHA = re.compile(r"^[0-9a-fA-F]+$")


def load_staging_evidence_policy(project_root: Path | str) -> dict:
    """读取独立版本化策略，避免校准器与评审器重复维护代表性门槛。"""

    root = Path(project_root).resolve()
    return yaml.safe_load(
        (
            root / "agent/contracts/agent_staging_evidence_policy.yml"
        ).read_text(encoding="utf-8")
    )


def _mapping(value: Any) -> Mapping:
    """把非字典输入收敛为空字典，使所有缺失字段形成确定性错误。"""

    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    """只接受非布尔正整数；字符串数字不会被静默转换。"""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _collect_forbidden_keys(value: Any, forbidden: set[str]) -> list[str]:
    """递归扫描键名，防止清单意外包含凭证、Prompt 或原始 Trace ID。"""

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


def validate_staging_evidence_manifest(
    manifest: Mapping,
    *,
    policy: Mapping,
) -> dict:
    """校验一份 Staging 清单并返回有界错误代码，不抛出内容型异常。"""

    errors: list[str] = []
    manifest_policy = _mapping(policy.get("manifest"))
    topology_policy = _mapping(policy.get("topology"))
    dependency_policy = _mapping(policy.get("dependencies"))
    workload_policy = _mapping(policy.get("workload"))
    tenancy_policy = _mapping(policy.get("tenancy"))
    audit_policy = _mapping(policy.get("audit"))
    privacy_policy = _mapping(policy.get("privacy"))

    if manifest.get("schema_version") != int(
        manifest_policy.get("schema_version", 0)
    ):
        errors.append("SCHEMA_VERSION_MISMATCH")
    if manifest.get("evidence_kind") != manifest_policy.get("evidence_kind"):
        errors.append("EVIDENCE_KIND_MISMATCH")

    environment = _mapping(manifest.get("environment"))
    environment_label = str(environment.get("label") or "").strip()
    required_prefix = str(
        manifest_policy.get("environment_label_prefix") or ""
    )
    if not required_prefix or not environment_label.startswith(required_prefix):
        errors.append("ENVIRONMENT_LABEL_NOT_REPRESENTATIVE_STAGING")

    git_sha = str(environment.get("git_sha") or "").strip()
    required_sha_length = int(manifest_policy.get("git_sha_length", 40))
    if len(git_sha) != required_sha_length or not _HEX_SHA.fullmatch(git_sha):
        errors.append("GIT_SHA_INVALID")
    if not str(environment.get("deployment_id") or "").strip():
        errors.append("DEPLOYMENT_ID_MISSING")
    captured_at = str(environment.get("captured_at") or "").strip()
    try:
        captured_datetime = datetime.fromisoformat(captured_at)
        captured_at_valid = captured_datetime.tzinfo is not None
    except ValueError:
        captured_at_valid = False
    if not captured_at_valid:
        errors.append("CAPTURED_AT_INVALID")

    topology = _mapping(manifest.get("topology"))
    replicas = _positive_int(topology.get("api_replicas"))
    workers = _positive_int(topology.get("workers_per_replica"))
    if replicas is None or replicas < int(
        topology_policy.get("minimum_api_replicas", 1)
    ):
        errors.append("API_REPLICA_COUNT_TOO_LOW")
    if workers is None or workers < int(
        topology_policy.get("minimum_workers_per_replica", 1)
    ):
        errors.append("WORKER_COUNT_TOO_LOW")
    if (
        replicas is None
        or workers is None
        or replicas * workers
        < int(topology_policy.get("minimum_total_api_processes", 1))
    ):
        errors.append("TOTAL_API_PROCESS_COUNT_TOO_LOW")
    if topology.get("traffic_backend") != topology_policy.get(
        "required_traffic_backend"
    ):
        errors.append("SHARED_REDIS_TRAFFIC_BACKEND_REQUIRED")
    if topology.get("shared_capacity_budget") is not True:
        errors.append("SHARED_CAPACITY_BUDGET_REQUIRED")
    if topology.get("persistent_audit_storage") is not True:
        errors.append("PERSISTENT_AUDIT_STORAGE_REQUIRED")
    if topology.get("audit_fail_closed") is not True:
        errors.append("AUDIT_FAIL_CLOSED_REQUIRED")

    dependencies = _mapping(manifest.get("dependencies"))
    required_authorities = tuple(
        str(value)
        for value in dependency_policy.get("required_live_authorities", ())
    )
    missing_authorities = sorted(
        authority
        for authority in required_authorities
        if dependencies.get(authority) != "live"
    )
    if missing_authorities:
        errors.append("LIVE_AUTHORITIES_INCOMPLETE")

    workload = _mapping(manifest.get("workload"))
    intent_counts = _mapping(workload.get("intent_request_counts"))
    normalized_counts: dict[str, int] = {}
    invalid_count = False
    for intent, value in intent_counts.items():
        count = _positive_int(value)
        if count is None:
            invalid_count = True
        else:
            normalized_counts[str(intent)] = count
    if invalid_count:
        errors.append("INTENT_REQUEST_COUNT_INVALID")
    total_requests = sum(normalized_counts.values())
    if total_requests < int(workload_policy.get("minimum_total_requests", 0)):
        errors.append("WORKLOAD_REQUEST_COUNT_TOO_LOW")
    minimum_per_intent = int(
        workload_policy.get("minimum_requests_per_required_intent", 1)
    )
    if any(
        normalized_counts.get(str(intent), 0) < minimum_per_intent
        for intent in workload_policy.get("required_intents", ())
    ):
        errors.append("REQUIRED_INTENT_MIX_INCOMPLETE")
    if workload.get("timeout_probe_passed") is not True:
        errors.append("TIMEOUT_PROBE_REQUIRED")
    if workload.get("admission_saturation_probe_passed") is not True:
        errors.append("ADMISSION_SATURATION_PROBE_REQUIRED")

    tenancy = _mapping(manifest.get("tenancy"))
    tenant_count = _positive_int(tenancy.get("tenant_count"))
    subject_count = _positive_int(tenancy.get("subject_count"))
    if tenant_count is None or tenant_count < int(
        tenancy_policy.get("minimum_tenants", 1)
    ):
        errors.append("TENANT_CARDINALITY_TOO_LOW")
    if subject_count is None or subject_count < int(
        tenancy_policy.get("minimum_subjects", 1)
    ):
        errors.append("SUBJECT_CARDINALITY_TOO_LOW")
    if tenancy.get("cross_tenant_isolation_probe_passed") is not True:
        errors.append("CROSS_TENANT_ISOLATION_PROBE_REQUIRED")

    audit = _mapping(manifest.get("audit"))
    raw_window_ms = audit.get("group_commit_window_ms")
    try:
        window_ms = (
            None if isinstance(raw_window_ms, bool) else float(raw_window_ms)
        )
    except (TypeError, ValueError):
        window_ms = None
    allowed_windows = {
        float(value)
        for value in audit_policy.get("allowed_group_commit_windows_ms", ())
    }
    if window_ms is None or window_ms not in allowed_windows:
        errors.append("AUDIT_GROUP_COMMIT_WINDOW_NOT_GOVERNED")
    raw_receipt_coverage = audit.get("runtime_audit_receipt_coverage")
    try:
        receipt_coverage = (
            None
            if isinstance(raw_receipt_coverage, bool)
            else float(raw_receipt_coverage)
        )
    except (TypeError, ValueError):
        receipt_coverage = None
    if receipt_coverage != float(
        audit_policy.get("required_runtime_audit_receipt_coverage", 1.0)
    ):
        errors.append("RUNTIME_AUDIT_RECEIPT_COVERAGE_INCOMPLETE")

    forbidden_keys = {
        str(value).strip().lower()
        for value in privacy_policy.get("forbidden_manifest_keys", ())
    }
    found_forbidden = sorted(set(_collect_forbidden_keys(manifest, forbidden_keys)))
    if found_forbidden:
        errors.append("FORBIDDEN_SENSITIVE_FIELD_PRESENT")

    return {
        "schema_version": 1,
        "validation_kind": "REPRESENTATIVE_STAGING_MANIFEST_VALIDATION_V1",
        "valid": not errors,
        "errors": errors,
        "environment_label": environment_label or None,
        "git_sha": git_sha or None,
        "deployment_id": str(environment.get("deployment_id") or "") or None,
        "audit_group_commit_window_ms": window_ms,
        "total_api_processes": (
            replicas * workers
            if replicas is not None and workers is not None
            else None
        ),
        "total_requests": total_requests,
        "tenant_count": tenant_count,
        "subject_count": subject_count,
        "live_authorities": sorted(
            authority
            for authority in required_authorities
            if dependencies.get(authority) == "live"
        ),
        "forbidden_sensitive_fields": found_forbidden,
        "production_slo_authority": False,
        "automatic_production_promotion": False,
    }


def validate_staging_evidence_file(
    project_root: Path | str,
    *,
    manifest_path: Path | str,
) -> dict:
    """读取 YAML/JSON 清单并使用项目策略验证。"""

    path = Path(manifest_path)
    manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise TypeError("Staging evidence manifest must be a mapping.")
    return validate_staging_evidence_manifest(
        manifest,
        policy=load_staging_evidence_policy(project_root),
    )


def write_validation_report(report: Mapping, output_path: Path | str) -> None:
    """把不含敏感原文的有界验证结果保存为 JSON。"""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
