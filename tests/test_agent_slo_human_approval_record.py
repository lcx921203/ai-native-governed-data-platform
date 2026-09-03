"""Agent SLO Human Approval Record V1 的确定性契约测试。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from acceptance.agent_slo.human_approval_record import (
    build_human_approval_record,
    canonical_json_sha256,
    load_human_approval_policy,
    validate_human_approval_decision,
    validate_human_approval_record,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _review() -> dict:
    """构造已通过 Representative Staging 机械门禁的 Promotion Review。"""

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
            "window_ms": 5.0,
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


def _decision(review: dict, *, value: str = "APPROVED") -> dict:
    """构造显式绑定 Review 的受信任 Human Decision。"""

    return {
        "schema_version": 1,
        "decision_kind": "AGENT_SLO_HUMAN_APPROVAL_DECISION_V1",
        "decision": value,
        "reason_code": (
            "EVIDENCE_REVIEWED_AND_ACCEPTED"
            if value == "APPROVED"
            else "EVIDENCE_REVIEWED_AND_REJECTED"
        ),
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
            "candidate_window_ms": 5.0,
            "git_sha": "a" * 40,
            "environment_label": "representative-staging-shared-redis",
        },
    }


def _policy() -> dict:
    return load_human_approval_policy(ROOT)


def test_approved_record_binds_exact_review_and_target_policy_without_applying_change():
    """批准只生成版本化授权证据，当前生产默认值仍保持 1 ms。"""

    review = _review()
    record = build_human_approval_record(
        review=review,
        decision=_decision(review),
        project_root=ROOT,
        policy=_policy(),
        generated_at=NOW,
    )

    assert record["approval_status"] == "HUMAN_APPROVAL_GRANTED"
    assert record["review"]["fingerprint_sha256"] == canonical_json_sha256(
        review
    )
    assert record["target"]["policy_path"] == (
        "agent/contracts/agent_audit_policy.yml"
    )
    assert record["target"]["field_path"] == (
        "runtime.default_group_commit_window_ms"
    )
    assert record["target"]["from_value"] == 1.0
    assert record["target"]["to_value"] == 5.0
    assert record["target"]["change_required"] is True
    assert record["authorization"]["versioned_change_authorized"] is True
    assert record["authorization"]["automatic_application"] is False
    assert record["authorization"]["production_default_updated"] is False
    assert len(record["record_fingerprint_sha256"]) == 64

    audit_policy = yaml.safe_load(
        (ROOT / "agent/contracts/agent_audit_policy.yml").read_text(
            encoding="utf-8"
        )
    )
    assert audit_policy["runtime"]["default_group_commit_window_ms"] == 1.0


def test_rejected_human_decision_is_valid_evidence_but_never_authorizes_change():
    """REJECTED 是合法审计结论，但不能被下游误读成可执行授权。"""

    review = _review()
    record = build_human_approval_record(
        review=review,
        decision=_decision(review, value="REJECTED"),
        project_root=ROOT,
        policy=_policy(),
        generated_at=NOW,
    )

    assert record["approval_status"] == "HUMAN_APPROVAL_REJECTED"
    assert record["decision"]["reason_code"] == (
        "EVIDENCE_REVIEWED_AND_REJECTED"
    )
    assert record["authorization"]["versioned_change_authorized"] is False
    assert record["authorization"]["no_change_required"] is False


def test_decision_must_bind_exact_review_candidate_sha_and_environment():
    """任何 Review 漂移都使原 Human Decision 失效。"""

    review = _review()
    decision = _decision(review)
    decision["review"]["fingerprint_sha256"] = "0" * 64
    decision["review"]["candidate_window_ms"] = 2.0
    decision["review"]["git_sha"] = "b" * 40
    decision["review"]["environment_label"] = "other-staging"

    validation = validate_human_approval_decision(
        decision,
        review=review,
        policy=_policy(),
    )

    assert validation["valid"] is False
    assert set(validation["errors"]) >= {
        "DECISION_REVIEW_FINGERPRINT_MISMATCH",
        "DECISION_CANDIDATE_WINDOW_MISMATCH",
        "DECISION_GIT_SHA_MISMATCH",
        "DECISION_ENVIRONMENT_LABEL_MISMATCH",
    }


def test_agent_unauthenticated_or_untrusted_identity_cannot_approve():
    """Agent 自批、未认证身份和非受信任 Identity Source 全部 Fail-Closed。"""

    review = _review()
    decision = _decision(review)
    decision["approver"] = {
        "subject_id": "agent-runtime",
        "actor_type": "AGENT",
        "authenticated": False,
        "identity_source": "SELF_ASSERTED",
    }
    decision["approval"]["decided_at"] = "2026-09-03T11:00:00+00:00"

    validation = validate_human_approval_decision(
        decision,
        review=review,
        policy=_policy(),
    )

    assert validation["valid"] is False
    assert set(validation["errors"]) >= {
        "APPROVER_MUST_BE_HUMAN_OPERATOR",
        "APPROVER_NOT_AUTHENTICATED",
        "APPROVER_IDENTITY_SOURCE_NOT_TRUSTED",
        "DECISION_PRECEDES_PROMOTION_REVIEW",
    }


def test_review_not_ready_or_staging_invalid_cannot_receive_human_approval():
    """人工批准不能绕过 Representative Staging 机械门禁。"""

    review = _review()
    decision = _decision(review)
    review["review_status"] = "STAGING_CALIBRATION_EVIDENCE_REQUIRED"
    review["representative_staging_calibration"]["valid"] = False
    # Review 改变后 Decision Fingerprint 也自然失配，但关键是 Review 本身不可批准。

    validation = validate_human_approval_decision(
        decision,
        review=review,
        policy=_policy(),
    )

    assert validation["valid"] is False
    assert "PROMOTION_REVIEW_NOT_APPROVABLE" in validation["errors"]


def test_record_tampering_target_drift_and_expiry_are_detected(tmp_path):
    """Record 指纹、目标策略文件和 TTL 构成后续执行前的三重再验证。"""

    # 构造最小仓库根，便于模拟目标策略文件在审批后漂移。
    root = tmp_path
    (root / "agent/contracts").mkdir(parents=True)
    policy_path = root / "agent/contracts/agent_audit_policy.yml"
    policy_path.write_text(
        "runtime:\n  default_group_commit_window_ms: 1.0\n",
        encoding="utf-8",
    )

    review = _review()
    record = build_human_approval_record(
        review=review,
        decision=_decision(review),
        project_root=root,
        policy=_policy(),
        generated_at=NOW,
    )

    valid = validate_human_approval_record(
        record,
        review=review,
        project_root=root,
        policy=_policy(),
        now=NOW,
    )
    assert valid["valid"] is True
    assert valid["versioned_change_eligible_now"] is True

    tampered = deepcopy(record)
    tampered["target"]["to_value"] = 2.0
    tampered_validation = validate_human_approval_record(
        tampered,
        review=review,
        project_root=root,
        policy=_policy(),
        now=NOW,
    )
    assert tampered_validation["valid"] is False
    assert "APPROVAL_RECORD_FINGERPRINT_MISMATCH" in tampered_validation[
        "errors"
    ]

    policy_path.write_text(
        "runtime:\n  default_group_commit_window_ms: 0.5\n",
        encoding="utf-8",
    )
    drifted = validate_human_approval_record(
        record,
        review=review,
        project_root=root,
        policy=_policy(),
        now=NOW,
    )
    assert drifted["valid"] is False
    assert set(drifted["errors"]) >= {
        "TARGET_POLICY_FILE_CHANGED_AFTER_APPROVAL",
        "TARGET_POLICY_VALUE_CHANGED_AFTER_APPROVAL",
    }

    # 恢复目标文件，再证明 24h TTL 过期后不能消费该授权。
    policy_path.write_text(
        "runtime:\n  default_group_commit_window_ms: 1.0\n",
        encoding="utf-8",
    )
    expired = validate_human_approval_record(
        record,
        review=review,
        project_root=root,
        policy=_policy(),
        now=NOW + timedelta(days=2),
    )
    assert expired["valid"] is False
    assert "APPROVAL_RECORD_EXPIRED" in expired["errors"]
    assert expired["versioned_change_eligible_now"] is False


def test_approved_noop_is_recorded_without_fake_change_authorization(tmp_path):
    """候选值已等于当前默认值时，批准有效但无需制造一次虚假的生产变更。"""

    root = tmp_path
    (root / "agent/contracts").mkdir(parents=True)
    (root / "agent/contracts/agent_audit_policy.yml").write_text(
        "runtime:\n  default_group_commit_window_ms: 5.0\n",
        encoding="utf-8",
    )
    review = _review()
    record = build_human_approval_record(
        review=review,
        decision=_decision(review),
        project_root=root,
        policy=_policy(),
        generated_at=NOW,
    )

    assert record["approval_status"] == "HUMAN_APPROVAL_GRANTED"
    assert record["target"]["change_required"] is False
    assert record["authorization"]["versioned_change_authorized"] is False
    assert record["authorization"]["no_change_required"] is True


def test_staging_review_policy_hands_off_to_human_approval_record():
    """Representative Staging Review 的成功出口必须明确指向 Human Approval Record。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_representative_staging_promotion_review_policy.yml"
        ).read_text(encoding="utf-8")
    )
    assert policy["promotion"]["ready_status"] == (
        "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL"
    )
    assert policy["promotion"]["next_consumer"] == (
        "AGENT_SLO_HUMAN_APPROVAL_RECORD_V1"
    )
