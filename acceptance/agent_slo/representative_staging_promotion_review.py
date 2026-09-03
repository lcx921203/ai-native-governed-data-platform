"""Representative Staging Promotion Review Integration V1（代表性预生产晋升评审集成）。

该模块不替代现有 ``Audit Group Commit Window Promotion Review V1``，而是在它已经完成
Lab Candidate 一致性检查与 Manifest 校验之后，再增加“真实 Representative Staging
Calibration Run”门禁。

最终只有同时满足：
- 多份 Audit Window Calibration V1 形成稳定候选窗口；
- Representative Staging Manifest V1 通过并与候选窗口 / Git SHA 一致；
- 至少三份互不重复的 Representative Staging Calibration Run V1 真实通过；
- Staging Run 的环境、部署、Git SHA、候选窗口、工作负载和关键 Coverage 全部可比；
才允许进入 ``STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL``。

机械评审仍不修改生产默认值，也不授予生产 SLO Authority。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from statistics import median
from typing import Any

import yaml

from .audit_window_promotion_review import build_promotion_review
from .staging_evidence_manifest import validate_staging_evidence_file


POLICY_PATH = (
    "agent/contracts/agent_representative_staging_promotion_review_policy.yml"
)


def load_representative_staging_promotion_review_policy(
    project_root: Path | str,
) -> dict:
    """读取独立版本化的 Representative Staging 晋升门禁策略。"""

    root = Path(project_root).resolve()
    return yaml.safe_load((root / POLICY_PATH).read_text(encoding="utf-8"))


def _mapping(value: Any) -> Mapping:
    """把非 Mapping 收敛为空 Mapping，避免内容型异常绕过 Fail-Closed 门禁。"""

    return value if isinstance(value, Mapping) else {}


def _float(value: Any) -> float | None:
    """读取有限数值；布尔值不能被静默当作 0/1。"""

    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _int(value: Any) -> int | None:
    """读取非布尔整数；非法类型返回 None，由上层形成机械门禁失败。"""

    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _evidence_identity(report: Mapping) -> str:
    """生成单次 Staging Run 的稳定去敏身份，用于拒绝重复证据。"""

    environment = _mapping(report.get("environment"))
    deployment_id = str(environment.get("deployment_id") or "").strip()
    generated_at = str(report.get("generated_at") or "").strip()
    if not deployment_id or not generated_at:
        return ""
    return f"staging:{deployment_id}:{generated_at}"


def _workload_signature(report: Mapping) -> tuple:
    """提取决定多次 Staging Run 是否可比较的非敏感工作负载签名。"""

    workload = _mapping(report.get("workload"))
    logical_counts = _mapping(workload.get("logical_intent_request_counts"))
    tenancy = _mapping(report.get("tenancy"))
    request_count = _int(workload.get("request_count"))
    tenant_count = _int(tenancy.get("planned_tenant_count"))
    subject_count = _int(tenancy.get("planned_subject_count"))
    normalized_counts: list[tuple[str, int | None]] = []
    for key, value in logical_counts.items():
        normalized_counts.append((str(key), _int(value)))
    return (
        request_count,
        tuple(sorted(normalized_counts)),
        tenant_count,
        subject_count,
    )


def _coverage_equals(
    container: Mapping,
    field: str,
    required: float,
) -> bool:
    """比较 Coverage；缺失、非数值或精度不一致都按不满足处理。"""

    value = _float(container.get(field))
    return value is not None and value == float(required)


def validate_representative_staging_calibration_evidence(
    reports: Iterable[Mapping],
    *,
    manifest_validation: Mapping,
    candidate_window_ms: float | None,
    policy: Mapping,
) -> dict:
    """校验多份真实 Staging Run，并只返回聚合后的机械门禁结果。"""

    items = list(reports)
    review_policy = _mapping(policy.get("review"))
    errors: list[str] = []

    minimum_count = int(
        review_policy.get("minimum_representative_staging_evidence_files", 1)
    )
    if len(items) < minimum_count:
        errors.append("STAGING_EVIDENCE_COUNT_TOO_LOW")

    required_kind = str(
        review_policy.get("required_staging_calibration_kind") or ""
    )
    required_schema = int(
        review_policy.get("required_staging_calibration_schema_version", 0)
    )
    required_status = str(
        review_policy.get("required_staging_calibration_status") or ""
    )

    identities: list[str] = []
    environment_labels: list[str] = []
    deployment_ids: list[str] = []
    git_shas: list[str] = []
    windows: list[float | None] = []
    workload_signatures: list[tuple] = []

    required_workload_coverage = float(
        review_policy.get("required_workload_coverage", 1.0)
    )
    required_runtime_audit_coverage = float(
        review_policy.get("required_runtime_audit_coverage", 1.0)
    )
    required_persistence_coverage = float(
        review_policy.get("required_persistence_receipt_coverage", 1.0)
    )
    required_probes = tuple(
        str(value) for value in review_policy.get("required_probes", ())
    )

    for report in items:
        if report.get("evidence_kind") != required_kind:
            errors.append("STAGING_EVIDENCE_KIND_MISMATCH")
        if _int(report.get("schema_version")) != required_schema:
            errors.append("STAGING_EVIDENCE_SCHEMA_VERSION_MISMATCH")
        if report.get("calibration_status") != required_status:
            errors.append("STAGING_CALIBRATION_STATUS_NOT_PASS")
        if report.get("production_slo_authority") is not False:
            errors.append("STAGING_EVIDENCE_CLAIMS_PRODUCTION_AUTHORITY")
        if report.get("production_default_updated") is not False:
            errors.append("STAGING_EVIDENCE_UPDATED_PRODUCTION_DEFAULT")

        promotion = _mapping(report.get("promotion"))
        if promotion.get("review_candidate_evidence") is not True:
            errors.append("STAGING_RUN_NOT_REVIEW_CANDIDATE")
        if promotion.get("automatic_production_promotion") is not False:
            errors.append("STAGING_RUN_AUTOMATIC_PROMOTION_NOT_FALSE")
        if promotion.get("production_default_updated") is not False:
            errors.append("STAGING_RUN_PROMOTION_UPDATED_DEFAULT")
        if promotion.get("production_slo_authority") is not False:
            errors.append("STAGING_RUN_PROMOTION_CLAIMS_AUTHORITY")

        identity = _evidence_identity(report)
        identities.append(identity)
        if not identity:
            errors.append("STAGING_EVIDENCE_IDENTITY_MISSING")

        environment = _mapping(report.get("environment"))
        environment_labels.append(
            str(environment.get("label") or "").strip()
        )
        deployment_ids.append(
            str(environment.get("deployment_id") or "").strip()
        )
        git_shas.append(str(environment.get("git_sha") or "").strip())
        windows.append(_float(environment.get("audit_group_commit_window_ms")))
        workload_signatures.append(_workload_signature(report))

        workload = _mapping(report.get("workload"))
        for field in (
            "response_contract_coverage",
            "runtime_intent_match_coverage",
            "tenant_subject_match_coverage",
            "answer_validated_coverage",
            "live_llm_call_coverage",
            "tool_result_coverage",
        ):
            if not _coverage_equals(
                workload,
                field,
                required_workload_coverage,
            ):
                errors.append("STAGING_WORKLOAD_COVERAGE_INCOMPLETE")
                break

        audit = _mapping(report.get("audit"))
        if not _coverage_equals(
            audit,
            "runtime_audit_coverage",
            required_runtime_audit_coverage,
        ):
            errors.append("STAGING_RUNTIME_AUDIT_COVERAGE_INCOMPLETE")
        if not _coverage_equals(
            audit,
            "persistence_receipt_coverage",
            required_persistence_coverage,
        ):
            errors.append("STAGING_PERSISTENCE_RECEIPT_COVERAGE_INCOMPLETE")

        probes = _mapping(report.get("probes"))
        if any(
            _mapping(probes.get(name)).get("passed") is not True
            for name in required_probes
        ):
            errors.append("STAGING_REQUIRED_PROBE_FAILED")

    if bool(review_policy.get("require_distinct_evidence_identities", True)):
        if identities and len(set(identities)) != len(identities):
            errors.append("STAGING_EVIDENCE_DUPLICATE")

    expected_label = str(
        manifest_validation.get("environment_label") or ""
    ).strip()
    expected_deployment = str(
        manifest_validation.get("deployment_id") or ""
    ).strip()
    expected_git_sha = str(manifest_validation.get("git_sha") or "").strip()
    expected_window = _float(
        manifest_validation.get("audit_group_commit_window_ms")
    )

    environment_matches = bool(items) and all(
        label == expected_label and bool(label)
        for label in environment_labels
    )
    deployment_matches = bool(items) and all(
        deployment_id == expected_deployment and bool(deployment_id)
        for deployment_id in deployment_ids
    )
    git_sha_matches = bool(items) and all(
        git_sha == expected_git_sha and bool(git_sha)
        for git_sha in git_shas
    )
    window_matches_manifest = bool(items) and expected_window is not None and all(
        value == expected_window for value in windows
    )
    window_matches_candidate = (
        bool(items)
        and candidate_window_ms is not None
        and all(value == float(candidate_window_ms) for value in windows)
    )

    if bool(review_policy.get("require_same_environment_label", True)) and not environment_matches:
        errors.append("STAGING_ENVIRONMENT_MISMATCH")
    if bool(review_policy.get("require_same_deployment_id", True)) and not deployment_matches:
        errors.append("STAGING_DEPLOYMENT_MISMATCH")
    if bool(review_policy.get("require_same_git_sha", True)) and not git_sha_matches:
        errors.append("STAGING_GIT_SHA_MISMATCH")
    if bool(review_policy.get("require_candidate_window_match", True)) and not (
        window_matches_manifest and window_matches_candidate
    ):
        errors.append("STAGING_CANDIDATE_WINDOW_MISMATCH")

    comparable_workload = bool(items) and len(set(workload_signatures)) == 1
    if bool(review_policy.get("require_comparable_workload_signature", True)) and not comparable_workload:
        errors.append("STAGING_WORKLOAD_NOT_COMPARABLE")

    manifest_total_requests = _int(manifest_validation.get("total_requests"))
    request_count_matches_manifest = (
        bool(items)
        and manifest_total_requests is not None
        and all(
            signature[0] == manifest_total_requests
            for signature in workload_signatures
        )
    )
    if bool(review_policy.get("require_request_count_matches_manifest", True)) and not request_count_matches_manifest:
        errors.append("STAGING_REQUEST_COUNT_MISMATCH")

    unique_errors = sorted(set(errors))
    valid = (
        manifest_validation.get("valid") is True
        and not unique_errors
        and len(items) >= minimum_count
    )

    return {
        "schema_version": 1,
        "validation_kind": (
            "REPRESENTATIVE_STAGING_PROMOTION_EVIDENCE_VALIDATION_V1"
        ),
        "valid": valid,
        "errors": unique_errors,
        "evidence_count": len(items),
        "minimum_evidence_count": minimum_count,
        "evidence_identities": identities,
        "environment_matches_manifest": environment_matches,
        "deployment_matches_manifest": deployment_matches,
        "git_sha_matches_manifest": git_sha_matches,
        "candidate_window_matches": (
            window_matches_manifest and window_matches_candidate
        ),
        "workload_comparable": comparable_workload,
        "request_count_matches_manifest": request_count_matches_manifest,
    }


def _staging_aggregate(reports: Iterable[Mapping]) -> dict | None:
    """把多次 Staging Run 的关键指标汇成供人工评审使用的有界摘要。"""

    items = list(reports)
    if not items:
        return None

    def values(section: str, field: str) -> list[float]:
        result: list[float] = []
        for report in items:
            raw = _mapping(report.get(section)).get(field)
            value = _float(raw)
            if value is not None:
                result.append(value)
        return result

    records_per_sync = values("audit", "runtime_records_per_sync")
    grouped_fraction = values("audit", "grouped_runtime_record_fraction")
    runtime_coverage = values("audit", "runtime_audit_coverage")
    persistence_coverage = values("audit", "persistence_receipt_coverage")
    llm_coverage = values("workload", "live_llm_call_coverage")
    tool_coverage = values("workload", "tool_result_coverage")

    durability_p95 = []
    batch_sync_p95 = []
    http_p95 = []
    runtime_p95 = []
    for report in items:
        audit = _mapping(report.get("audit"))
        workload = _mapping(report.get("workload"))
        for target, container, field in (
            (durability_p95, _mapping(audit.get("durability_wait_latency_ms")), "p95"),
            (batch_sync_p95, _mapping(audit.get("batch_sync_latency_ms")), "p95"),
            (http_p95, _mapping(workload.get("http_latency_ms")), "p95"),
            (runtime_p95, _mapping(workload.get("runtime_latency_ms")), "p95"),
        ):
            value = _float(container.get(field))
            if value is not None:
                target.append(value)

    return {
        "evidence_count": len(items),
        "runtime_records_per_sync_median": (
            round(median(records_per_sync), 6) if records_per_sync else None
        ),
        "grouped_runtime_record_fraction_median": (
            round(median(grouped_fraction), 6) if grouped_fraction else None
        ),
        "runtime_audit_coverage_min": (
            min(runtime_coverage) if runtime_coverage else None
        ),
        "persistence_receipt_coverage_min": (
            min(persistence_coverage) if persistence_coverage else None
        ),
        "live_llm_call_coverage_min": min(llm_coverage) if llm_coverage else None,
        "tool_result_coverage_min": min(tool_coverage) if tool_coverage else None,
        "durability_wait_p95_ms_median": (
            round(median(durability_p95), 6) if durability_p95 else None
        ),
        "batch_sync_p95_ms_median": (
            round(median(batch_sync_p95), 6) if batch_sync_p95 else None
        ),
        "http_p95_ms_median": round(median(http_p95), 6) if http_p95 else None,
        "runtime_p95_ms_median": (
            round(median(runtime_p95), 6) if runtime_p95 else None
        ),
    }


def build_representative_staging_promotion_review(
    calibration_reports: Iterable[Mapping],
    *,
    staging_manifest_validation: Mapping,
    staging_calibration_reports: Iterable[Mapping],
    base_review_policy: Mapping,
    staging_review_policy: Mapping,
) -> dict:
    """复用现有 Promotion Review，再强制加入真实 Staging Run 门禁。"""

    calibration_items = list(calibration_reports)
    staging_items = list(staging_calibration_reports)
    base_policy = _mapping(base_review_policy)

    review = build_promotion_review(
        calibration_items,
        minimum_evidence_count=int(
            base_policy["minimum_calibration_evidence_files"]
        ),
        representative_staging_label_prefix=str(
            base_policy["representative_staging_label_prefix"]
        ),
        require_same_git_sha=bool(base_policy["require_same_git_sha"]),
        staging_manifest_validation=staging_manifest_validation,
    )

    candidate_window_ms = _float(
        _mapping(review.get("candidate_consensus")).get("window_ms")
    )
    staging_validation = validate_representative_staging_calibration_evidence(
        staging_items,
        manifest_validation=staging_manifest_validation,
        candidate_window_ms=candidate_window_ms,
        policy=staging_review_policy,
    )
    staging_policy = _mapping(staging_review_policy.get("promotion"))

    base_status = str(review.get("review_status") or "")
    if base_status == "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL":
        review["review_status"] = (
            str(staging_policy["ready_status"])
            if staging_validation["valid"]
            else str(staging_policy["missing_or_invalid_staging_run_status"])
        )

    review["representative_staging_calibration"] = {
        **staging_validation,
        "aggregate": _staging_aggregate(staging_items),
    }

    decision = dict(_mapping(review.get("decision")))
    decision["human_approval_required"] = True
    decision["automatic_production_promotion"] = False
    decision["production_default_updated"] = False
    decision["production_slo_authority"] = False
    if (
        review["review_status"]
        == staging_policy.get("missing_or_invalid_staging_run_status")
    ):
        decision["reason"] = (
            "Representative staging Manifest passed, but repeated live staging "
            "calibration evidence is missing or failed mechanical gates."
        )
    elif review["review_status"] == staging_policy.get("ready_status"):
        decision["reason"] = (
            "Lab candidate consensus, representative staging Manifest, and repeated "
            "live staging calibration evidence passed mechanical review; explicit "
            "human approval is still required."
        )
    review["decision"] = decision
    return review


def run_representative_staging_promotion_review(
    project_root: Path | str,
    *,
    calibration_evidence_paths: Iterable[Path | str],
    staging_manifest_path: Path | str,
    staging_evidence_paths: Iterable[Path | str],
    output_path: Path | str,
) -> dict:
    """读取三类版本化证据并生成最终人工晋升评审 JSON。"""

    root = Path(project_root).resolve()
    base_payload = yaml.safe_load(
        (
            root / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )
    base_review_policy = base_payload[
        "audit_group_commit_window_promotion_review_v1"
    ]
    staging_review_policy = load_representative_staging_promotion_review_policy(
        root
    )

    calibration_reports = [
        json.loads(Path(path).read_text(encoding="utf-8"))
        for path in calibration_evidence_paths
    ]
    staging_reports = [
        json.loads(Path(path).read_text(encoding="utf-8"))
        for path in staging_evidence_paths
    ]
    manifest_validation = validate_staging_evidence_file(
        root,
        manifest_path=staging_manifest_path,
    )

    review = build_representative_staging_promotion_review(
        calibration_reports,
        staging_manifest_validation=manifest_validation,
        staging_calibration_reports=staging_reports,
        base_review_policy=base_review_policy,
        staging_review_policy=staging_review_policy,
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return review
