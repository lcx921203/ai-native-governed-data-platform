"""Agent SLO Human Approval Record V1（人工审批记录）。

该模块消费已经达到 ``STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL`` 的 Promotion Review
以及由受信任审批渠道提供的 Human Decision，生成一个版本化、可复核、不可自动执行的
Approval Record。

核心边界：
- Human Decision 必须绑定 Promotion Review 的 Canonical SHA-256、候选窗口、Git SHA 与环境；
- Record 再绑定目标 ``agent_audit_policy.yml`` 的精确文件 SHA-256 与当前默认值；
- ``APPROVED`` 只产生“允许后续版本化变更”的证据，不直接改生产配置；
- 审批记录指纹用于篡改检测，不是人的密码学身份签名；
- 后续生产变更消费者必须重新验证 Review、Record、目标策略文件与审批有效期。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml


POLICY_PATH = "agent/contracts/agent_slo_human_approval_policy.yml"


def load_human_approval_policy(project_root: Path | str) -> dict:
    """读取版本化 Human Approval Policy。"""

    root = Path(project_root).resolve()
    return yaml.safe_load((root / POLICY_PATH).read_text(encoding="utf-8"))


def _mapping(value: Any) -> Mapping:
    """把非 Mapping 收敛为空 Mapping，使缺失字段形成确定性失败。"""

    return value if isinstance(value, Mapping) else {}


def _finite_float(value: Any) -> float | None:
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


def _parse_datetime(value: Any) -> datetime | None:
    """只接受带时区 ISO-8601 时间。"""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _canonical_json_bytes(value: Mapping) -> bytes:
    """把 JSON 语义规范化后用于稳定 Evidence Fingerprint。"""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json_sha256(value: Mapping) -> str:
    """计算不受 JSON 缩进/键顺序影响的 Canonical SHA-256。"""

    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path | str) -> str:
    """计算目标策略文件精确字节 SHA-256；任何文件漂移都会改变结果。"""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _collect_forbidden_keys(value: Any, forbidden: set[str]) -> list[str]:
    """递归扫描禁止进入 Approval Record 的敏感字段名。"""

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


def _review_context(review: Mapping, policy: Mapping) -> dict:
    """提取 Human Decision 必须显式绑定的有限 Review Context。"""

    errors: list[str] = []
    review_policy = _mapping(policy.get("review"))

    if review.get("evidence_kind") != review_policy.get("required_evidence_kind"):
        errors.append("PROMOTION_REVIEW_EVIDENCE_KIND_MISMATCH")
    if review.get("review_status") != review_policy.get("required_ready_status"):
        errors.append("PROMOTION_REVIEW_NOT_READY_FOR_HUMAN_APPROVAL")
    if (
        bool(review_policy.get("require_representative_staging", True))
        and review.get("representative_staging") is not True
    ):
        errors.append("REPRESENTATIVE_STAGING_NOT_PROVEN")

    staging = _mapping(review.get("representative_staging_calibration"))
    if (
        bool(
            review_policy.get(
                "require_representative_staging_calibration_valid",
                True,
            )
        )
        and staging.get("valid") is not True
    ):
        errors.append("REPRESENTATIVE_STAGING_CALIBRATION_NOT_VALID")

    decision = _mapping(review.get("decision"))
    required_false_fields = (
        (
            "automatic_production_promotion",
            "PROMOTION_REVIEW_AUTOMATIC_PROMOTION_NOT_FALSE",
        ),
        (
            "production_default_updated",
            "PROMOTION_REVIEW_PRODUCTION_DEFAULT_ALREADY_UPDATED",
        ),
        (
            "production_slo_authority",
            "PROMOTION_REVIEW_CLAIMS_PRODUCTION_AUTHORITY",
        ),
    )
    for field, error in required_false_fields:
        if decision.get(field) is not False:
            errors.append(error)
    if (
        bool(review_policy.get("require_human_approval_required", True))
        and decision.get("human_approval_required") is not True
    ):
        errors.append("PROMOTION_REVIEW_DOES_NOT_REQUIRE_HUMAN_APPROVAL")

    candidate_window_ms = _finite_float(
        _mapping(review.get("candidate_consensus")).get("window_ms")
    )
    if candidate_window_ms is None:
        errors.append("PROMOTION_REVIEW_CANDIDATE_WINDOW_MISSING")

    git_sha = str(review.get("git_sha") or "").strip()
    required_sha_length = int(review_policy.get("git_sha_length", 40))
    if (
        len(git_sha) != required_sha_length
        or not re.fullmatch(r"[0-9a-fA-F]+", git_sha)
    ):
        errors.append("PROMOTION_REVIEW_GIT_SHA_INVALID")

    labels = review.get("environment_labels")
    if not isinstance(labels, list):
        labels = []
    normalized_labels = [str(value or "").strip() for value in labels]
    unique_labels = {value for value in normalized_labels if value}
    environment_label = next(iter(unique_labels)) if len(unique_labels) == 1 else ""
    if (
        bool(review_policy.get("require_single_environment_label", True))
        and not environment_label
    ):
        errors.append("PROMOTION_REVIEW_ENVIRONMENT_LABEL_NOT_UNIQUE")

    generated_at = _parse_datetime(review.get("generated_at"))
    if generated_at is None:
        errors.append("PROMOTION_REVIEW_GENERATED_AT_INVALID")

    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "fingerprint_sha256": canonical_json_sha256(review),
        "candidate_window_ms": candidate_window_ms,
        "git_sha": git_sha,
        "environment_label": environment_label,
        "generated_at": (
            generated_at.isoformat() if generated_at is not None else ""
        ),
        "staging_evidence_count": int(staging.get("evidence_count") or 0),
    }


def validate_human_approval_decision(
    decision: Mapping,
    *,
    review: Mapping,
    policy: Mapping,
) -> dict:
    """验证 Human Decision 是否由受信任的人类身份对精确 Review 作出。"""

    errors: list[str] = []
    decision_policy = _mapping(policy.get("decision"))
    review_context = _review_context(review, policy)
    if not review_context["valid"]:
        errors.append("PROMOTION_REVIEW_NOT_APPROVABLE")

    if decision.get("schema_version") != int(
        decision_policy.get("schema_version", 0)
    ):
        errors.append("DECISION_SCHEMA_VERSION_MISMATCH")
    if decision.get("decision_kind") != decision_policy.get("decision_kind"):
        errors.append("DECISION_KIND_MISMATCH")

    decision_value = str(decision.get("decision") or "").strip()
    allowed_decisions = {
        str(value) for value in decision_policy.get("allowed_decisions", ())
    }
    if decision_value not in allowed_decisions:
        errors.append("DECISION_VALUE_NOT_ALLOWED")

    reason_code = str(decision.get("reason_code") or "").strip()
    # YAML 中 reason code 列表是 list；这里只接受版本化策略中该 Decision 对应的代码。
    raw_reason_codes = _mapping(decision_policy.get("allowed_reason_codes")).get(
        decision_value,
        (),
    )
    if not isinstance(raw_reason_codes, list):
        raw_reason_codes = []
    if reason_code not in {str(value) for value in raw_reason_codes}:
        errors.append("DECISION_REASON_CODE_NOT_ALLOWED")

    approver = _mapping(decision.get("approver"))
    subject_id = str(approver.get("subject_id") or "").strip()
    subject_pattern = str(decision_policy.get("subject_id_pattern") or "")
    if not subject_pattern or not re.fullmatch(subject_pattern, subject_id):
        errors.append("APPROVER_SUBJECT_ID_INVALID")
    if approver.get("actor_type") != decision_policy.get("required_actor_type"):
        errors.append("APPROVER_MUST_BE_HUMAN_OPERATOR")
    if (
        bool(decision_policy.get("require_authenticated", True))
        and approver.get("authenticated") is not True
    ):
        errors.append("APPROVER_NOT_AUTHENTICATED")
    identity_source = str(approver.get("identity_source") or "").strip()
    trusted_sources = {
        str(value)
        for value in decision_policy.get("trusted_identity_sources", ())
    }
    if identity_source not in trusted_sources:
        errors.append("APPROVER_IDENTITY_SOURCE_NOT_TRUSTED")

    approval = _mapping(decision.get("approval"))
    external_decision_id = str(
        approval.get("external_decision_id") or ""
    ).strip()
    external_pattern = str(
        decision_policy.get("external_decision_id_pattern") or ""
    )
    if not external_pattern or not re.fullmatch(
        external_pattern,
        external_decision_id,
    ):
        errors.append("EXTERNAL_DECISION_ID_INVALID")

    decided_at = _parse_datetime(approval.get("decided_at"))
    if decided_at is None:
        errors.append("DECIDED_AT_INVALID")
    review_generated_at = _parse_datetime(review_context.get("generated_at"))
    if (
        decided_at is not None
        and review_generated_at is not None
        and bool(decision_policy.get("require_decision_not_before_review", True))
        and decided_at < review_generated_at
    ):
        errors.append("DECISION_PRECEDES_PROMOTION_REVIEW")

    decision_review = _mapping(decision.get("review"))
    if (
        str(decision_review.get("fingerprint_sha256") or "").lower()
        != str(review_context["fingerprint_sha256"]).lower()
    ):
        errors.append("DECISION_REVIEW_FINGERPRINT_MISMATCH")
    if _finite_float(decision_review.get("candidate_window_ms")) != (
        review_context["candidate_window_ms"]
    ):
        errors.append("DECISION_CANDIDATE_WINDOW_MISMATCH")
    if str(decision_review.get("git_sha") or "").strip() != review_context[
        "git_sha"
    ]:
        errors.append("DECISION_GIT_SHA_MISMATCH")
    if (
        str(decision_review.get("environment_label") or "").strip()
        != review_context["environment_label"]
    ):
        errors.append("DECISION_ENVIRONMENT_LABEL_MISMATCH")

    return {
        "schema_version": 1,
        "validation_kind": "AGENT_SLO_HUMAN_APPROVAL_DECISION_VALIDATION_V1",
        "valid": not errors,
        "errors": sorted(set(errors)),
        "decision": decision_value,
        "reason_code": reason_code,
        "subject_id": subject_id,
        "actor_type": str(approver.get("actor_type") or ""),
        "authenticated": approver.get("authenticated") is True,
        "identity_source": identity_source,
        "external_decision_id": external_decision_id,
        "decided_at": decided_at.isoformat() if decided_at is not None else "",
        "review": review_context,
    }


def _read_target_context(project_root: Path, policy: Mapping) -> dict:
    """读取将被后续版本化修改的生产 Guardrail 当前状态。"""

    target_policy = _mapping(policy.get("target"))
    policy_rel = str(target_policy.get("policy_path") or "").strip()
    field_path = str(target_policy.get("field_path") or "").strip()
    target_path = project_root / policy_rel
    if not policy_rel or not target_path.is_file():
        raise ValueError("Human approval target policy file is missing.")

    payload = yaml.safe_load(target_path.read_text(encoding="utf-8"))
    current: Any = payload
    for part in field_path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError("Human approval target field path is missing.")
        current = current[part]
    current_value = _finite_float(current)
    if (
        bool(target_policy.get("numeric_value_required", True))
        and current_value is None
    ):
        raise ValueError("Human approval target current value is not numeric.")

    return {
        "change_kind": str(target_policy.get("change_kind") or ""),
        "policy_path": policy_rel,
        "field_path": field_path,
        "policy_file_sha256": file_sha256(target_path),
        "current_value": current_value,
    }


def _record_fingerprint(record: Mapping) -> str:
    """计算 Record 自身指纹；fingerprint 字段本身不参与哈希。"""

    payload = deepcopy(dict(record))
    payload.pop("record_fingerprint_sha256", None)
    return canonical_json_sha256(payload)


def build_human_approval_record(
    *,
    review: Mapping,
    decision: Mapping,
    project_root: Path | str,
    policy: Mapping,
    generated_at: datetime | None = None,
) -> dict:
    """生成绑定 Review + Target Policy 的不可自动执行 Approval Record。"""

    root = Path(project_root).resolve()
    validation = validate_human_approval_decision(
        decision,
        review=review,
        policy=policy,
    )
    if validation["valid"] is not True:
        raise ValueError(
            "Human approval decision failed validation: "
            + ",".join(validation["errors"])
        )

    record_policy = _mapping(policy.get("record"))
    promotion_policy = _mapping(policy.get("promotion"))
    decision_policy = _mapping(policy.get("decision"))
    target = _read_target_context(root, policy)
    review_context = validation["review"]

    decision_value = str(validation["decision"])
    approved = decision_value == "APPROVED"
    current_value = _finite_float(target["current_value"])
    candidate_value = _finite_float(review_context["candidate_window_ms"])
    change_required = (
        current_value is not None
        and candidate_value is not None
        and current_value != candidate_value
    )

    decided_at = _parse_datetime(validation["decided_at"])
    if decided_at is None:
        raise ValueError("Validated human approval decision is missing decided_at.")
    expires_at = decided_at + timedelta(
        minutes=int(decision_policy.get("approval_ttl_minutes", 1440))
    )
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        raise ValueError("generated_at must include timezone information.")
    if generated < decided_at:
        raise ValueError("Human approval record cannot be generated before the decision.")

    record = {
        "schema_version": int(record_policy.get("schema_version", 1)),
        "evidence_kind": str(record_policy.get("evidence_kind") or ""),
        "approval_status": (
            str(record_policy.get("approved_status") or "")
            if approved
            else str(record_policy.get("rejected_status") or "")
        ),
        "generated_at": generated.isoformat(),
        "review": {
            "evidence_kind": review.get("evidence_kind"),
            "review_status": review.get("review_status"),
            "fingerprint_sha256": review_context["fingerprint_sha256"],
            "generated_at": review_context["generated_at"],
            "git_sha": review_context["git_sha"],
            "environment_label": review_context["environment_label"],
            "candidate_window_ms": candidate_value,
            "representative_staging_evidence_count": review_context[
                "staging_evidence_count"
            ],
        },
        "approver": {
            "subject_id": validation["subject_id"],
            "actor_type": validation["actor_type"],
            "authenticated": validation["authenticated"],
            "identity_source": validation["identity_source"],
            "external_decision_id": validation["external_decision_id"],
        },
        "decision": {
            "value": decision_value,
            "reason_code": validation["reason_code"],
            "decided_at": validation["decided_at"],
            "expires_at": expires_at.isoformat(),
        },
        "target": {
            "change_kind": target["change_kind"],
            "policy_path": target["policy_path"],
            "field_path": target["field_path"],
            "policy_file_sha256": target["policy_file_sha256"],
            "from_value": current_value,
            "to_value": candidate_value,
            "change_required": change_required,
        },
        "authorization": {
            "versioned_change_authorized": approved and change_required,
            "no_change_required": approved and not change_required,
            "automatic_application": False,
            "production_default_updated": False,
            "production_slo_authority": False,
            "approval_record_is_identity_signature": False,
            "next_consumer": promotion_policy.get("next_consumer"),
        },
    }
    record["record_fingerprint_sha256"] = _record_fingerprint(record)
    return record


def validate_human_approval_record(
    record: Mapping,
    *,
    review: Mapping,
    project_root: Path | str,
    policy: Mapping,
    now: datetime | None = None,
) -> dict:
    """供后续 Production Guardrail Change 在执行前重新验证 Approval Record。"""

    errors: list[str] = []
    root = Path(project_root).resolve()
    record_policy = _mapping(policy.get("record"))
    target_policy = _mapping(policy.get("target"))
    review_context = _review_context(review, policy)

    if record.get("schema_version") != int(record_policy.get("schema_version", 0)):
        errors.append("APPROVAL_RECORD_SCHEMA_VERSION_MISMATCH")
    if record.get("evidence_kind") != record_policy.get("evidence_kind"):
        errors.append("APPROVAL_RECORD_EVIDENCE_KIND_MISMATCH")

    expected_fingerprint = _record_fingerprint(record)
    actual_fingerprint = str(record.get("record_fingerprint_sha256") or "")
    if actual_fingerprint != expected_fingerprint:
        errors.append("APPROVAL_RECORD_FINGERPRINT_MISMATCH")

    review_record = _mapping(record.get("review"))
    if not review_context["valid"]:
        errors.append("BOUND_PROMOTION_REVIEW_NO_LONGER_APPROVABLE")
    if review_record.get("fingerprint_sha256") != review_context.get(
        "fingerprint_sha256"
    ):
        errors.append("APPROVAL_RECORD_REVIEW_FINGERPRINT_MISMATCH")
    if _finite_float(review_record.get("candidate_window_ms")) != review_context.get(
        "candidate_window_ms"
    ):
        errors.append("APPROVAL_RECORD_CANDIDATE_WINDOW_MISMATCH")
    if review_record.get("git_sha") != review_context.get("git_sha"):
        errors.append("APPROVAL_RECORD_GIT_SHA_MISMATCH")
    if review_record.get("environment_label") != review_context.get(
        "environment_label"
    ):
        errors.append("APPROVAL_RECORD_ENVIRONMENT_MISMATCH")

    decision_policy = _mapping(policy.get("decision"))
    approver = _mapping(record.get("approver"))
    subject_id = str(approver.get("subject_id") or "").strip()
    subject_pattern = str(decision_policy.get("subject_id_pattern") or "")
    if not subject_pattern or not re.fullmatch(subject_pattern, subject_id):
        errors.append("APPROVAL_RECORD_APPROVER_SUBJECT_INVALID")
    if approver.get("actor_type") != decision_policy.get("required_actor_type"):
        errors.append("APPROVAL_RECORD_APPROVER_NOT_HUMAN")
    if (
        bool(decision_policy.get("require_authenticated", True))
        and approver.get("authenticated") is not True
    ):
        errors.append("APPROVAL_RECORD_APPROVER_NOT_AUTHENTICATED")
    identity_source = str(approver.get("identity_source") or "").strip()
    if identity_source not in {
        str(value)
        for value in decision_policy.get("trusted_identity_sources", ())
    }:
        errors.append("APPROVAL_RECORD_IDENTITY_SOURCE_NOT_TRUSTED")
    external_id = str(approver.get("external_decision_id") or "").strip()
    external_pattern = str(
        decision_policy.get("external_decision_id_pattern") or ""
    )
    if not external_pattern or not re.fullmatch(external_pattern, external_id):
        errors.append("APPROVAL_RECORD_EXTERNAL_DECISION_ID_INVALID")

    target = _mapping(record.get("target"))
    current_target = _read_target_context(root, policy)
    if target.get("change_kind") != target_policy.get("change_kind"):
        errors.append("APPROVAL_RECORD_CHANGE_KIND_MISMATCH")
    if target.get("policy_path") != current_target["policy_path"]:
        errors.append("APPROVAL_RECORD_POLICY_PATH_MISMATCH")
    if target.get("field_path") != current_target["field_path"]:
        errors.append("APPROVAL_RECORD_FIELD_PATH_MISMATCH")
    if target.get("policy_file_sha256") != current_target["policy_file_sha256"]:
        errors.append("TARGET_POLICY_FILE_CHANGED_AFTER_APPROVAL")
    if _finite_float(target.get("from_value")) != _finite_float(
        current_target["current_value"]
    ):
        errors.append("TARGET_POLICY_VALUE_CHANGED_AFTER_APPROVAL")
    if _finite_float(target.get("to_value")) != review_context.get(
        "candidate_window_ms"
    ):
        errors.append("APPROVAL_RECORD_TARGET_VALUE_MISMATCH")

    decision = _mapping(record.get("decision"))
    approved = record.get("approval_status") == record_policy.get("approved_status")
    rejected = record.get("approval_status") == record_policy.get("rejected_status")
    if not approved and not rejected:
        errors.append("APPROVAL_RECORD_STATUS_INVALID")
    decision_value = str(decision.get("value") or "")
    if approved and decision_value != "APPROVED":
        errors.append("APPROVAL_RECORD_DECISION_STATUS_MISMATCH")
    if rejected and decision_value != "REJECTED":
        errors.append("APPROVAL_RECORD_DECISION_STATUS_MISMATCH")
    raw_reason_codes = _mapping(
        decision_policy.get("allowed_reason_codes")
    ).get(decision_value, ())
    if not isinstance(raw_reason_codes, list):
        raw_reason_codes = []
    if str(decision.get("reason_code") or "") not in {
        str(value) for value in raw_reason_codes
    }:
        errors.append("APPROVAL_RECORD_REASON_CODE_INVALID")

    decided_at = _parse_datetime(decision.get("decided_at"))
    expires_at = _parse_datetime(decision.get("expires_at"))
    if decided_at is None:
        errors.append("APPROVAL_RECORD_DECIDED_AT_INVALID")
    if expires_at is None:
        errors.append("APPROVAL_RECORD_EXPIRES_AT_INVALID")
    if decided_at is not None and expires_at is not None:
        expected_expiry = decided_at + timedelta(
            minutes=int(decision_policy.get("approval_ttl_minutes", 1440))
        )
        if expires_at != expected_expiry:
            errors.append("APPROVAL_RECORD_EXPIRY_MISMATCH")

    generated_at_value = _parse_datetime(record.get("generated_at"))
    if generated_at_value is None:
        errors.append("APPROVAL_RECORD_GENERATED_AT_INVALID")
    elif decided_at is not None and generated_at_value < decided_at:
        errors.append("APPROVAL_RECORD_GENERATED_BEFORE_DECISION")

    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("now must include timezone information.")
    expired = expires_at is not None and checked_at > expires_at
    if approved and expired:
        errors.append("APPROVAL_RECORD_EXPIRED")

    authorization = _mapping(record.get("authorization"))
    expected_change_required = _finite_float(target.get("from_value")) != _finite_float(
        target.get("to_value")
    )
    expected_authorized = approved and expected_change_required and not expired
    if authorization.get("automatic_application") is not False:
        errors.append("APPROVAL_RECORD_AUTOMATIC_APPLICATION_NOT_FALSE")
    if authorization.get("production_default_updated") is not False:
        errors.append("APPROVAL_RECORD_CLAIMS_PRODUCTION_UPDATE")
    if authorization.get("production_slo_authority") is not False:
        errors.append("APPROVAL_RECORD_CLAIMS_PRODUCTION_AUTHORITY")
    if bool(authorization.get("versioned_change_authorized")) != (
        approved and expected_change_required
    ):
        errors.append("APPROVAL_RECORD_AUTHORIZATION_FLAG_INVALID")

    forbidden = {
        str(value).strip().lower()
        for value in _mapping(policy.get("privacy")).get(
            "forbidden_record_keys",
            (),
        )
    }
    forbidden_found = sorted(set(_collect_forbidden_keys(record, forbidden)))
    if forbidden_found:
        errors.append("APPROVAL_RECORD_FORBIDDEN_FIELD_PRESENT")

    unique_errors = sorted(set(errors))
    return {
        "schema_version": 1,
        "validation_kind": "AGENT_SLO_HUMAN_APPROVAL_RECORD_VALIDATION_V1",
        "valid": not unique_errors,
        "errors": unique_errors,
        "approval_status": record.get("approval_status"),
        "record_fingerprint_valid": actual_fingerprint == expected_fingerprint,
        "review_binding_valid": not any(
            error.startswith("APPROVAL_RECORD_REVIEW_")
            or error.startswith("APPROVAL_RECORD_CANDIDATE_")
            or error.startswith("APPROVAL_RECORD_GIT_")
            or error.startswith("APPROVAL_RECORD_ENVIRONMENT_")
            for error in unique_errors
        ),
        "target_policy_unchanged": not any(
            error in {
                "TARGET_POLICY_FILE_CHANGED_AFTER_APPROVAL",
                "TARGET_POLICY_VALUE_CHANGED_AFTER_APPROVAL",
            }
            for error in unique_errors
        ),
        "expired": expired,
        "versioned_change_eligible_now": expected_authorized and not unique_errors,
    }


def run_human_approval_record(
    project_root: Path | str,
    *,
    promotion_review_path: Path | str,
    decision_path: Path | str,
    output_path: Path | str,
    generated_at: datetime | None = None,
) -> dict:
    """读取 Promotion Review + Human Decision 并输出 Human Approval Record V1。"""

    root = Path(project_root).resolve()
    policy = load_human_approval_policy(root)
    review = json.loads(Path(promotion_review_path).read_text(encoding="utf-8"))
    if not isinstance(review, Mapping):
        raise TypeError("Promotion review must be a JSON mapping.")
    decision = yaml.safe_load(Path(decision_path).read_text(encoding="utf-8"))
    if not isinstance(decision, Mapping):
        raise TypeError("Human approval decision must be a YAML mapping.")

    record = build_human_approval_record(
        review=review,
        decision=decision,
        project_root=root,
        policy=policy,
        generated_at=generated_at,
    )

    # 在写出前做一次自验证；使用 generated_at 避免单元测试跨时间边界。
    checked_at = generated_at or datetime.now(timezone.utc)
    validation = validate_human_approval_record(
        record,
        review=review,
        project_root=root,
        policy=policy,
        now=checked_at,
    )
    if validation["valid"] is not True:
        raise RuntimeError(
            "Generated human approval record failed self-validation: "
            + ",".join(validation["errors"])
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record
