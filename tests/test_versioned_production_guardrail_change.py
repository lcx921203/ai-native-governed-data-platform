"""Versioned Production Guardrail Change V1 的确定性契约测试。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from acceptance.agent_slo.human_approval_record import (
    build_human_approval_record,
    canonical_json_sha256,
    load_human_approval_policy,
)
from acceptance.agent_slo.versioned_production_guardrail_change import (
    build_versioned_production_guardrail_change,
    load_production_guardrail_change_policy,
    validate_versioned_production_guardrail_package,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _review(candidate: float = 5.0) -> dict:
    return {
        "schema_version": 1,
        "evidence_kind": "AUDIT_GROUP_COMMIT_WINDOW_PROMOTION_REVIEW_V1",
        "review_status": "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL",
        "generated_at": "2026-09-03T11:30:00+00:00",
        "environment_labels": [
            "representative-staging-shared-redis",
            "representative-staging-shared-redis",
            "representative-staging-shared-redis",
        ],
        "git_sha": "a" * 40,
        "representative_staging": True,
        "candidate_consensus": {
            "stable": True,
            "window_ms": candidate,
            "supporting_evidence_count": 3,
        },
        "representative_staging_calibration": {
            "valid": True,
            "evidence_count": 3,
            "errors": [],
        },
        "decision": {
            "human_approval_required": True,
            "automatic_production_promotion": False,
            "production_default_updated": False,
            "production_slo_authority": False,
        },
    }


def _decision(review: dict) -> dict:
    candidate = review["candidate_consensus"]["window_ms"]
    return {
        "schema_version": 1,
        "decision_kind": "AGENT_SLO_HUMAN_APPROVAL_DECISION_V1",
        "decision": "APPROVED",
        "reason_code": "EVIDENCE_REVIEWED_AND_ACCEPTED",
        "approver": {
            "subject_id": "platform-approver-01",
            "actor_type": "HUMAN_OPERATOR",
            "authenticated": True,
            "identity_source": "AUTHENTICATED_UPSTREAM",
        },
        "approval": {
            "external_decision_id": "change-review/20260903/001",
            "decided_at": "2026-09-03T11:45:00+00:00",
        },
        "review": {
            "fingerprint_sha256": canonical_json_sha256(review),
            "candidate_window_ms": candidate,
            "git_sha": "a" * 40,
            "environment_label": "representative-staging-shared-redis",
        },
    }


def _approval(root: Path, review: dict) -> dict:
    return build_human_approval_record(
        review=review,
        decision=_decision(review),
        project_root=root,
        policy=load_human_approval_policy(root),
        generated_at=NOW,
    )


def _materialize_root(
    root: Path,
    *,
    default_value: float = 1.0,
    max_value: float = 5.0,
    candidates: list[float] | None = None,
) -> None:
    """构造最小治理根目录，用于模拟漂移、No-op 与候选集合变化。"""

    contracts = root / "agent/contracts"
    contracts.mkdir(parents=True, exist_ok=True)
    for name in (
        "agent_slo_human_approval_policy.yml",
        "agent_production_guardrail_change_policy.yml",
    ):
        (contracts / name).write_text(
            (ROOT / "agent/contracts" / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (contracts / "agent_audit_policy.yml").write_text(
        "version: 5\n"
        "runtime:\n"
        f"  default_group_commit_window_ms: {default_value}\n"
        f"  max_group_commit_window_ms: {max_value}\n",
        encoding="utf-8",
    )
    candidate_values = candidates or [0.0, 0.5, 1.0, 2.0, 5.0]
    (contracts / "agent_slo_calibration_policy.yml").write_text(
        yaml.safe_dump(
            {
                "audit_group_commit_window_calibration_v1": {
                    "candidate_windows_ms": candidate_values
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_approved_change_generates_proposal_without_mutating_repository_target():
    """Human Approval 只能生成 proposed 文件内容，仓库当前默认值必须保持 1 ms。"""

    review = _review()
    approval = _approval(ROOT, review)
    policy = load_production_guardrail_change_policy(ROOT)
    target_path = ROOT / "agent/contracts/agent_audit_policy.yml"
    before_text = target_path.read_text(encoding="utf-8")

    record, proposed = build_versioned_production_guardrail_change(
        project_root=ROOT,
        promotion_review=review,
        approval_record=approval,
        policy=policy,
        now=NOW,
    )

    assert record["change_status"] == "CHANGE_PACKAGE_READY_FOR_MANUAL_APPLICATION"
    assert record["target"]["from_value"] == 1.0
    assert record["target"]["to_value"] == 5.0
    assert record["target"]["changed_semantic_paths"] == [
        "runtime.default_group_commit_window_ms"
    ]
    assert record["application"]["automatic_application"] is False
    assert record["application"]["repository_target_overwritten"] is False
    assert record["application"]["production_default_updated"] is False
    assert proposed is not None
    assert "default_group_commit_window_ms: 5.0" in proposed
    assert target_path.read_text(encoding="utf-8") == before_text

    validation = validate_versioned_production_guardrail_package(
        record=record,
        proposed_text=proposed,
        project_root=ROOT,
        policy=policy,
    )
    assert validation["valid"] is True


def test_tampered_proposal_or_record_fails_package_validation():
    """提案多改字段或 Change Record 被篡改都必须 Fail-Closed。"""

    review = _review()
    approval = _approval(ROOT, review)
    policy = load_production_guardrail_change_policy(ROOT)
    record, proposed = build_versioned_production_guardrail_change(
        project_root=ROOT,
        promotion_review=review,
        approval_record=approval,
        policy=policy,
        now=NOW,
    )
    assert proposed is not None

    tampered_proposal = proposed.replace(
        "max_group_commit_window_ms: 5.0",
        "max_group_commit_window_ms: 4.0",
    )
    invalid_proposal = validate_versioned_production_guardrail_package(
        record=record,
        proposed_text=tampered_proposal,
        project_root=ROOT,
        policy=policy,
    )
    assert invalid_proposal["valid"] is False
    assert "CHANGE_PACKAGE_CHANGED_UNAPPROVED_FIELD" in invalid_proposal["errors"]

    tampered_record = deepcopy(record)
    tampered_record["target"]["to_value"] = 2.0
    invalid_record = validate_versioned_production_guardrail_package(
        record=tampered_record,
        proposed_text=proposed,
        project_root=ROOT,
        policy=policy,
    )
    assert invalid_record["valid"] is False
    assert "CHANGE_RECORD_FINGERPRINT_MISMATCH" in invalid_record["errors"]


def test_expired_or_drifted_human_approval_cannot_generate_change(tmp_path):
    """审批过期或审批后目标策略漂移时，下游必须重新走审批。"""

    root = tmp_path / "repo"
    _materialize_root(root)
    review = _review()
    approval = _approval(root, review)
    policy = load_production_guardrail_change_policy(root)

    with pytest.raises(ValueError, match="failed revalidation"):
        build_versioned_production_guardrail_change(
            project_root=root,
            promotion_review=review,
            approval_record=approval,
            policy=policy,
            now=NOW + timedelta(days=2),
        )

    target_path = root / "agent/contracts/agent_audit_policy.yml"
    target_path.write_text(
        target_path.read_text(encoding="utf-8").replace(
            "default_group_commit_window_ms: 1.0",
            "default_group_commit_window_ms: 0.5",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="failed revalidation"):
        build_versioned_production_guardrail_change(
            project_root=root,
            promotion_review=review,
            approval_record=approval,
            policy=policy,
            now=NOW,
        )


def test_candidate_must_remain_in_governed_calibration_set(tmp_path):
    """即使 Human Approval 有效，治理候选集合变化后旧目标值也不能继续生成包。"""

    root = tmp_path / "repo"
    _materialize_root(root)
    review = _review()
    approval = _approval(root, review)

    # 审批完成后，版本化校准策略删除 5 ms；Approval 仍绑定目标文件，但 Change Gate 要重验候选集合。
    _materialize_root(root, candidates=[0.0, 0.5, 1.0, 2.0])
    policy = load_production_guardrail_change_policy(root)
    with pytest.raises(ValueError, match="calibration set"):
        build_versioned_production_guardrail_change(
            project_root=root,
            promotion_review=review,
            approval_record=approval,
            policy=policy,
            now=NOW,
        )


def test_approved_noop_generates_no_fake_policy_proposal(tmp_path):
    """候选值已经是当前默认值时只形成 NO_CHANGE_REQUIRED，不生成 proposed 文件。"""

    root = tmp_path / "repo"
    _materialize_root(root, default_value=5.0)
    review = _review()
    approval = _approval(root, review)
    record, proposed = build_versioned_production_guardrail_change(
        project_root=root,
        promotion_review=review,
        approval_record=approval,
        policy=load_production_guardrail_change_policy(root),
        now=NOW,
    )

    assert record["change_status"] == "NO_CHANGE_REQUIRED"
    assert record["target"]["change_required"] is False
    assert record["application"]["proposal_generated"] is False
    assert proposed is None


def test_change_policy_locks_manual_application_boundary():
    """V1 契约必须明确包生成 != 自动生产变更。"""

    policy = load_production_guardrail_change_policy(ROOT)
    package = policy["package"]
    assert policy["version"] == 1
    assert package["evidence_kind"] == "VERSIONED_PRODUCTION_GUARDRAIL_CHANGE_V1"
    assert package["automatic_application"] is False
    assert package["repository_target_overwritten"] is False
    assert package["production_default_updated"] is False
    assert package["manual_application_required"] is True

    approval_policy = load_human_approval_policy(ROOT)
    assert approval_policy["promotion"]["next_consumer"] == (
        "VERSIONED_PRODUCTION_GUARDRAIL_CHANGE_V1"
    )
