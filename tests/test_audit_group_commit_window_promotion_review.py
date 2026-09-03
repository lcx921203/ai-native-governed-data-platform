"""Audit Group Commit Window Promotion Review V1 的确定性契约测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from acceptance.agent_slo.audit_window_promotion_review import (
    build_promotion_review,
)


ROOT = Path(__file__).resolve().parents[1]


def _report(run_id: str, *, label: str = "github-hosted-lab") -> dict:
    """构造一份选择 5 ms 的完整 Calibration V1 汇总证据。"""

    windows = [0.0, 0.5, 1.0, 2.0, 5.0]
    return {
        "schema_version": 1,
        "evidence_kind": "AUDIT_GROUP_COMMIT_WINDOW_CALIBRATION_V1",
        "calibration_status": "LAB_CANDIDATE",
        "production_slo_authority": False,
        "production_default_updated": False,
        "generated_at": f"2026-09-03T09:{run_id}:00+00:00",
        "environment": {
            "label": label,
            "github_run_id": run_id,
            "git_sha": "99136c692834eae98ade6dfba5ee49442a91f681",
        },
        "method": {
            "scenario": "authenticated-baseline",
            "candidate_windows_ms": windows,
            "baseline_window_ms": 0.0,
            "efficiency_metric": "runtime_records_per_sync_median",
            "latency_metric": "durability_wait_p95_ms_median",
            "efficiency_plateau_ratio": 0.95,
            "selection_rule": "smallest eligible window reaching plateau",
        },
        "window_results": [
            {
                "window_ms": window,
                "eligible": True,
                "runtime_audit_receipt_coverage_min": 1.0,
                "runtime_records_per_sync_median": 4.0 + window,
                "durability_wait_p95_ms_median": 1.0 + window,
                "durability_wait_p95_ms_worst": 2.0 + window,
            }
            for window in windows
        ],
        "selection": {
            "lab_candidate_window_ms": 5.0,
            "automatic_production_promotion": False,
        },
    }


def _review(reports: list[dict]) -> dict:
    """使用版本化策略中的核心门槛调用纯函数。"""

    return build_promotion_review(
        reports,
        minimum_evidence_count=3,
        representative_staging_label_prefix="representative-staging",
        require_same_git_sha=True,
    )


def test_three_lab_runs_produce_review_only_without_default_update():
    """三次一致的 GitHub Lab 结果只能形成实验室评审，不得晋升生产。"""

    review = _review([_report("101"), _report("102"), _report("103")])

    assert review["review_status"] == "LAB_REVIEW_ONLY"
    assert review["candidate_consensus"] == {
        "stable": True,
        "window_ms": 5.0,
        "supporting_evidence_count": 3,
    }
    assert review["selected_window_aggregate"][
        "runtime_audit_receipt_coverage_min"
    ] == 1.0
    assert review["decision"]["human_approval_required"] is True
    assert review["decision"]["automatic_production_promotion"] is False
    assert review["decision"]["production_default_updated"] is False


def test_representative_staging_is_ready_only_for_human_approval():
    """代表性 Staging 证据通过机械门禁后，仍只能等待人工批准。"""

    reports = [
        _report(str(run_id), label="representative-staging-shared-redis")
        for run_id in (201, 202, 203)
    ]
    review = _review(reports)

    assert (
        review["review_status"]
        == "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL"
    )
    assert review["representative_staging"] is True
    assert review["decision"]["production_slo_authority"] is False


def test_conflicting_candidate_is_not_ready():
    """候选窗口不一致时不得给出多数票式晋升结论。"""

    reports = [_report("301"), _report("302"), _report("303")]
    reports[2]["selection"]["lab_candidate_window_ms"] = 2.0
    review = _review(reports)

    assert review["review_status"] == "NOT_READY"
    assert review["candidate_consensus"]["stable"] is False
    assert review["candidate_consensus"]["window_ms"] is None


def test_duplicate_run_or_mixed_method_fails_closed():
    """重复证据和不同实验方法都必须直接失败，不能静默合并。"""

    duplicate = _report("401")
    with pytest.raises(ValueError, match="duplicate"):
        _review([duplicate, deepcopy(duplicate), _report("402")])

    reports = [_report("501"), _report("502"), _report("503")]
    reports[2]["method"]["candidate_windows_ms"] = [0.0, 1.0, 5.0]
    with pytest.raises(ValueError, match="not comparable"):
        _review(reports)


def test_v8_policy_locks_review_gate_and_preserves_production_default():
    """V8 契约固定三份证据、同 SHA、代表性 Staging 与人工批准门禁。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )
    review = policy["audit_group_commit_window_promotion_review_v1"]
    assert policy["version"] == 8
    assert review["minimum_calibration_evidence_files"] == 3
    assert review["require_same_git_sha"] is True
    assert review["candidate_consensus_ratio"] == 1.0
    assert review["automatic_production_promotion"] is False
    assert review["explicit_human_approval_required"] is True

    audit_policy = yaml.safe_load(
        (ROOT / "agent/contracts/agent_audit_policy.yml").read_text(
            encoding="utf-8"
        )
    )
    assert audit_policy["runtime"]["default_group_commit_window_ms"] == 1.0
