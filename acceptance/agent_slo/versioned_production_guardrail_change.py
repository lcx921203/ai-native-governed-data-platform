"""Versioned Production Guardrail Change V1（版本化生产 Guardrail 变更包）。

该模块消费 Promotion Review + Human Approval Record，重新验证审批、目标策略文件与审批
有效期，然后生成一个“待人工应用”的版本化变更包。它不会覆盖仓库中的生产策略文件。

变更包包含：
- 去敏 Change Record JSON；
- proposed/ 下的目标策略候选文件；
- before/after SHA-256 与唯一允许字段的 from/to 值。

核心边界：APPROVED != APPLIED。生成包不等于生产变更，更不授予 Agent 自动修改生产配置的
能力。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .human_approval_record import (
    canonical_json_sha256,
    file_sha256,
    load_human_approval_policy,
    validate_human_approval_record,
)


POLICY_PATH = "agent/contracts/agent_production_guardrail_change_policy.yml"


def load_production_guardrail_change_policy(project_root: Path | str) -> dict:
    """读取版本化生产 Guardrail Change Policy。"""

    root = Path(project_root).resolve()
    return yaml.safe_load((root / POLICY_PATH).read_text(encoding="utf-8"))


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _nested_value(payload: Mapping, dotted_path: str) -> Any:
    current: Any = payload
    for part in str(dotted_path).split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"Missing governed field path: {dotted_path}")
        current = current[part]
    return current


def _deep_diff_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """返回语义发生变化的叶子字段路径，用于证明只改审批字段。"""

    if isinstance(before, Mapping) and isinstance(after, Mapping):
        paths: list[str] = []
        keys = set(before) | set(after)
        for key in sorted(keys, key=str):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                paths.append(child)
                continue
            paths.extend(_deep_diff_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list) and isinstance(after, list):
        if before == after:
            return []
        return [prefix]
    return [] if before == after else [prefix]


def _format_numeric(value: float) -> str:
    """稳定输出浮点 YAML 标量；整数值仍保留 .0，便于当前策略风格一致。"""

    value = float(value)
    if value.is_integer():
        return f"{value:.1f}"
    text = f"{value:.12f}".rstrip("0").rstrip(".")
    return text


def _replace_yaml_numeric_scalar(
    source_text: str,
    *,
    field_path: str,
    from_value: float,
    to_value: float,
) -> str:
    """只替换目标 Key 的单行数值，保留注释、缩进和其他原始字节。"""

    key = str(field_path).split(".")[-1]
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*){re.escape(key)}:[ \t]*(?P<value>[^#\r\n]+?)"
        rf"(?P<comment>[ \t]*#.*)?$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(source_text))
    if len(matches) != 1:
        raise ValueError(
            "Approved YAML target key must appear exactly once in the target policy file."
        )

    match = matches[0]
    parsed = yaml.safe_load(match.group("value").strip())
    parsed_value = _finite_float(parsed)
    if parsed_value != float(from_value):
        raise ValueError("Approved YAML source scalar no longer matches from_value.")

    replacement = (
        f"{match.group('indent')}{key}: {_format_numeric(to_value)}"
        f"{match.group('comment') or ''}"
    )
    return source_text[: match.start()] + replacement + source_text[match.end() :]


def _collect_forbidden_keys(value: Any, forbidden: set[str]) -> list[str]:
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


def _package_fingerprint(record: Mapping) -> str:
    payload = deepcopy(dict(record))
    payload.pop("package_fingerprint_sha256", None)
    return canonical_json_sha256(payload)


def _read_calibration_candidates(root: Path, policy: Mapping) -> tuple[list[float], str]:
    calibration = _mapping(policy.get("calibration"))
    rel_path = str(calibration.get("policy_path") or "").strip()
    candidate_path = str(calibration.get("candidate_values_path") or "").strip()
    path = root / rel_path
    if not rel_path or not path.is_file():
        raise ValueError("Calibration policy file is missing.")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw = _nested_value(payload, candidate_path)
    if not isinstance(raw, list) or not raw:
        raise ValueError("Calibration candidate set is missing or empty.")
    candidates: list[float] = []
    for item in raw:
        value = _finite_float(item)
        if value is None:
            raise ValueError("Calibration candidate set contains a non-numeric value.")
        candidates.append(value)
    return candidates, file_sha256(path)


def _target_context(root: Path, policy: Mapping) -> dict:
    target_policy = _mapping(policy.get("target"))
    rel_path = str(target_policy.get("policy_path") or "").strip()
    field_path = str(target_policy.get("field_path") or "").strip()
    maximum_field_path = str(target_policy.get("maximum_field_path") or "").strip()
    path = root / rel_path
    if not rel_path or not path.is_file():
        raise ValueError("Production Guardrail target policy file is missing.")

    source_text = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(source_text)
    if not isinstance(payload, Mapping):
        raise ValueError("Production Guardrail target policy must be a YAML mapping.")

    current = _finite_float(_nested_value(payload, field_path))
    maximum = _finite_float(_nested_value(payload, maximum_field_path))
    if current is None or maximum is None:
        raise ValueError("Production Guardrail current/max values must be numeric.")

    return {
        "path": path,
        "policy_path": rel_path,
        "field_path": field_path,
        "maximum_field_path": maximum_field_path,
        "source_text": source_text,
        "payload": payload,
        "current_value": current,
        "maximum_value": maximum,
        "sha256": file_sha256(path),
    }


def build_versioned_production_guardrail_change(
    *,
    project_root: Path | str,
    promotion_review: Mapping,
    approval_record: Mapping,
    policy: Mapping,
    now: datetime | None = None,
) -> tuple[dict, str | None]:
    """构建 Change Record 与 proposed target text；不写仓库目标文件。"""

    root = Path(project_root).resolve()
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        raise ValueError("now must include timezone information.")

    approval_policy = load_human_approval_policy(root)
    approval_validation = validate_human_approval_record(
        approval_record,
        review=promotion_review,
        project_root=root,
        policy=approval_policy,
        now=checked_at,
    )
    if approval_validation.get("valid") is not True:
        raise ValueError(
            "Human approval record failed revalidation: "
            + ",".join(approval_validation.get("errors") or ())
        )

    approval_gate = _mapping(policy.get("approval"))
    if approval_record.get("evidence_kind") != approval_gate.get(
        "required_record_kind"
    ):
        raise ValueError("Unsupported human approval record kind.")
    if approval_record.get("approval_status") != approval_gate.get("required_status"):
        raise ValueError("Human approval record is not APPROVED.")

    authorization = _mapping(approval_record.get("authorization"))
    target_record = _mapping(approval_record.get("target"))
    no_change_required = authorization.get("no_change_required") is True
    versioned_change_authorized = (
        authorization.get("versioned_change_authorized") is True
    )
    if no_change_required and versioned_change_authorized:
        raise ValueError("Approval record has conflicting change authorization flags.")
    if not no_change_required and bool(
        approval_gate.get("require_versioned_change_authorized", True)
    ) and not versioned_change_authorized:
        raise ValueError("Human approval does not authorize a versioned change.")
    if authorization.get("next_consumer") != approval_gate.get(
        "required_next_consumer"
    ):
        raise ValueError("Human approval record next_consumer mismatch.")

    target = _target_context(root, policy)
    target_policy = _mapping(policy.get("target"))
    if target_record.get("change_kind") != target_policy.get("change_kind"):
        raise ValueError("Approved change kind does not match Guardrail Change policy.")
    if target_record.get("policy_path") != target["policy_path"]:
        raise ValueError("Approved target policy path mismatch.")
    if target_record.get("field_path") != target["field_path"]:
        raise ValueError("Approved target field path mismatch.")
    if target_record.get("policy_file_sha256") != target["sha256"]:
        raise ValueError("Target policy SHA-256 changed after human approval.")

    from_value = _finite_float(target_record.get("from_value"))
    to_value = _finite_float(target_record.get("to_value"))
    if from_value is None or to_value is None:
        raise ValueError("Approved Guardrail from/to values must be numeric.")
    if from_value != target["current_value"]:
        raise ValueError("Target policy current value changed after human approval.")

    minimum = _finite_float(target_policy.get("minimum_value_ms"))
    if minimum is None:
        raise ValueError("Guardrail minimum bound is invalid.")
    if not minimum <= to_value <= target["maximum_value"]:
        raise ValueError("Approved target value is outside governed runtime bounds.")

    candidates, calibration_policy_sha256 = _read_calibration_candidates(root, policy)
    if bool(
        _mapping(policy.get("calibration")).get(
            "approved_value_must_be_candidate",
            True,
        )
    ) and to_value not in candidates:
        raise ValueError("Approved target value is not in the governed calibration set.")

    review_fingerprint = canonical_json_sha256(promotion_review)
    approval_fingerprint = str(
        approval_record.get("record_fingerprint_sha256") or ""
    ).strip()
    if not approval_fingerprint:
        raise ValueError("Human approval record fingerprint is missing.")

    package_policy = _mapping(policy.get("package"))
    change_required = from_value != to_value
    if no_change_required != (not change_required):
        raise ValueError("Human approval no-change flag does not match current target state.")

    proposed_text: str | None = None
    after_sha256 = target["sha256"]
    changed_paths: list[str] = []
    status = str(package_policy.get("no_change_status") or "NO_CHANGE_REQUIRED")

    if change_required:
        proposed_text = _replace_yaml_numeric_scalar(
            target["source_text"],
            field_path=target["field_path"],
            from_value=from_value,
            to_value=to_value,
        )
        proposed_payload = yaml.safe_load(proposed_text)
        changed_paths = _deep_diff_paths(target["payload"], proposed_payload)
        if changed_paths != [target["field_path"]]:
            raise RuntimeError(
                "Generated proposal changed fields outside the approved Guardrail path."
            )
        if _finite_float(_nested_value(proposed_payload, target["field_path"])) != to_value:
            raise RuntimeError("Generated proposal does not contain the approved target value.")
        after_sha256 = hashlib.sha256(proposed_text.encode("utf-8")).hexdigest()
        status = str(
            package_policy.get("ready_status")
            or "CHANGE_PACKAGE_READY_FOR_MANUAL_APPLICATION"
        )

    record = {
        "schema_version": int(package_policy.get("schema_version", 1)),
        "evidence_kind": str(package_policy.get("evidence_kind") or ""),
        "change_status": status,
        "generated_at": checked_at.isoformat(),
        "bindings": {
            "promotion_review_sha256": review_fingerprint,
            "human_approval_record_sha256": approval_fingerprint,
            "human_approval_policy_sha256": file_sha256(
                root / str(approval_gate.get("policy_path") or "")
            ),
            "calibration_policy_sha256": calibration_policy_sha256,
            "change_policy_sha256": file_sha256(root / POLICY_PATH),
        },
        "approval": {
            "approval_status": approval_record.get("approval_status"),
            "external_decision_id": _mapping(approval_record.get("approver")).get(
                "external_decision_id"
            ),
            "decided_at": _mapping(approval_record.get("decision")).get(
                "decided_at"
            ),
            "expires_at": _mapping(approval_record.get("decision")).get(
                "expires_at"
            ),
            "revalidated_at": checked_at.isoformat(),
            "versioned_change_eligible_now": approval_validation.get(
                "versioned_change_eligible_now"
            ),
        },
        "target": {
            "change_kind": target_policy.get("change_kind"),
            "policy_path": target["policy_path"],
            "field_path": target["field_path"],
            "from_value": from_value,
            "to_value": to_value,
            "change_required": change_required,
            "before_file_sha256": target["sha256"],
            "after_file_sha256": after_sha256,
            "changed_semantic_paths": changed_paths,
            "maximum_governed_value_ms": target["maximum_value"],
            "approved_value_is_calibration_candidate": to_value in candidates,
        },
        "application": {
            "proposal_generated": proposed_text is not None,
            "proposal_root": package_policy.get("proposal_root"),
            "manual_application_required": bool(
                package_policy.get("manual_application_required", True)
            ),
            "automatic_application": False,
            "repository_target_overwritten": False,
            "production_default_updated": False,
            "production_slo_authority": False,
        },
    }
    record["package_fingerprint_sha256"] = _package_fingerprint(record)

    forbidden = {
        str(value).strip().lower()
        for value in _mapping(policy.get("privacy")).get(
            "forbidden_record_keys",
            (),
        )
    }
    if _collect_forbidden_keys(record, forbidden):
        raise RuntimeError("Guardrail change record contains a forbidden field.")
    return record, proposed_text


def validate_versioned_production_guardrail_package(
    *,
    record: Mapping,
    proposed_text: str | None,
    project_root: Path | str,
    policy: Mapping,
) -> dict:
    """验证 Change Record + Proposal 的完整性；不会应用 Proposal。"""

    errors: list[str] = []
    root = Path(project_root).resolve()
    package_policy = _mapping(policy.get("package"))
    target = _target_context(root, policy)

    if record.get("schema_version") != int(package_policy.get("schema_version", 0)):
        errors.append("CHANGE_RECORD_SCHEMA_VERSION_MISMATCH")
    if record.get("evidence_kind") != package_policy.get("evidence_kind"):
        errors.append("CHANGE_RECORD_EVIDENCE_KIND_MISMATCH")
    if str(record.get("package_fingerprint_sha256") or "") != _package_fingerprint(
        record
    ):
        errors.append("CHANGE_RECORD_FINGERPRINT_MISMATCH")

    target_record = _mapping(record.get("target"))
    if target_record.get("before_file_sha256") != target["sha256"]:
        errors.append("CHANGE_PACKAGE_BEFORE_POLICY_DRIFTED")
    if _finite_float(target_record.get("from_value")) != target["current_value"]:
        errors.append("CHANGE_PACKAGE_BEFORE_VALUE_DRIFTED")

    change_required = target_record.get("change_required") is True
    if change_required:
        if proposed_text is None:
            errors.append("CHANGE_PACKAGE_PROPOSAL_MISSING")
        else:
            try:
                proposed_payload = yaml.safe_load(proposed_text)
                changed_paths = _deep_diff_paths(target["payload"], proposed_payload)
                if changed_paths != [target["field_path"]]:
                    errors.append("CHANGE_PACKAGE_CHANGED_UNAPPROVED_FIELD")
                if _finite_float(
                    _nested_value(proposed_payload, target["field_path"])
                ) != _finite_float(target_record.get("to_value")):
                    errors.append("CHANGE_PACKAGE_PROPOSAL_VALUE_MISMATCH")
                proposed_sha = hashlib.sha256(
                    proposed_text.encode("utf-8")
                ).hexdigest()
                if proposed_sha != target_record.get("after_file_sha256"):
                    errors.append("CHANGE_PACKAGE_AFTER_SHA256_MISMATCH")
            except (TypeError, ValueError, yaml.YAMLError):
                errors.append("CHANGE_PACKAGE_PROPOSAL_INVALID_YAML")
    else:
        if proposed_text is not None:
            errors.append("NO_CHANGE_PACKAGE_MUST_NOT_HAVE_PROPOSAL")

    application = _mapping(record.get("application"))
    for field, code in (
        ("automatic_application", "CHANGE_PACKAGE_AUTOMATIC_APPLICATION_NOT_FALSE"),
        (
            "repository_target_overwritten",
            "CHANGE_PACKAGE_REPOSITORY_OVERWRITE_NOT_FALSE",
        ),
        (
            "production_default_updated",
            "CHANGE_PACKAGE_CLAIMS_PRODUCTION_UPDATE",
        ),
        (
            "production_slo_authority",
            "CHANGE_PACKAGE_CLAIMS_PRODUCTION_AUTHORITY",
        ),
    ):
        if application.get(field) is not False:
            errors.append(code)

    return {
        "schema_version": 1,
        "validation_kind": "VERSIONED_PRODUCTION_GUARDRAIL_CHANGE_VALIDATION_V1",
        "valid": not errors,
        "errors": sorted(set(errors)),
        "change_status": record.get("change_status"),
        "proposal_present": proposed_text is not None,
        "repository_target_unchanged": True,
    }


def run_versioned_production_guardrail_change(
    project_root: Path | str,
    *,
    promotion_review_path: Path | str,
    approval_record_path: Path | str,
    output_dir: Path | str,
    now: datetime | None = None,
) -> dict:
    """生成受治理 Change Package；输出目录可以归档/评审，但不覆盖仓库目标。"""

    root = Path(project_root).resolve()
    policy = load_production_guardrail_change_policy(root)
    review = json.loads(Path(promotion_review_path).read_text(encoding="utf-8"))
    approval = json.loads(Path(approval_record_path).read_text(encoding="utf-8"))
    if not isinstance(review, Mapping) or not isinstance(approval, Mapping):
        raise TypeError("Promotion Review and Human Approval Record must be JSON mappings.")

    before_sha256 = file_sha256(
        root / str(_mapping(policy.get("target")).get("policy_path") or "")
    )
    record, proposed_text = build_versioned_production_guardrail_change(
        project_root=root,
        promotion_review=review,
        approval_record=approval,
        policy=policy,
        now=now,
    )
    after_generation_sha256 = file_sha256(
        root / str(_mapping(policy.get("target")).get("policy_path") or "")
    )
    if before_sha256 != after_generation_sha256:
        raise RuntimeError("Guardrail package generator mutated the repository target.")

    validation = validate_versioned_production_guardrail_package(
        record=record,
        proposed_text=proposed_text,
        project_root=root,
        policy=policy,
    )
    if validation["valid"] is not True:
        raise RuntimeError(
            "Generated Guardrail change package failed self-validation: "
            + ",".join(validation["errors"])
        )

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    record_name = str(
        _mapping(policy.get("package")).get("record_file_name")
        or "versioned-production-guardrail-change-v1.json"
    )
    (out / record_name).write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if proposed_text is not None:
        proposal_root = str(
            _mapping(policy.get("package")).get("proposal_root") or "proposed"
        )
        target_rel = str(_mapping(policy.get("target")).get("policy_path") or "")
        proposed_path = out / proposal_root / target_rel
        proposed_path.parent.mkdir(parents=True, exist_ok=True)
        proposed_path.write_text(proposed_text, encoding="utf-8")

    return record
