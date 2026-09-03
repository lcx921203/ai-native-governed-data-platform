"""Representative Staging Promotion Review Integration V1 的确定性契约测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from acceptance.agent_slo.representative_staging_promotion_review import (
    build_representative_staging_promotion_review,
    load_representative_staging_promotion_review_policy,
    validate_representative_staging_calibration_evidence,
)

ROOT = Path(__file__).resolve().parents[1]


def _base_review_policy() -> dict:
    """读取现有 Audit Window Promotion Review V1，避免测试复制生产门槛。"""

    payload = yaml.safe_load(
        (
            ROOT / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )
    return payload["audit_group_commit_window_promotion_review_v1"]


def _manifest_validation() -> dict:
    """构造已通过 Manifest V1 Validator 的最小去敏结果。"""

    return {
        "valid": True,
        "errors": [],
        "environment_label": "representative-staging-shared-redis",
        "deployment_id": "staging-release-20260903-03",
        "git_sha": "c" * 40,
        "audit_group_commit_window_ms": 5.0,
        "total_requests": 100,
        "tenant_count": 2,
        "subject_count": 4,
    }


def _calibration_report(run_id: str) -> dict:
    """构造一份稳定选择 5 ms 的 Calibration V1 汇总证据。"""

    windows = [0.0, 0.5, 1.0, 2.0, 5.0]
    return {
        "schema_version": 1,
        "evidence_kind": "AUDIT_GROUP_COMMIT_WINDOW_CALIBRATION_V1",
        "calibration_status": "LAB_CANDIDATE",
        "production_slo_authority": False,
        "production_default_updated": False,
        "generated_at": f"2026-09-03T11:0{run_id}:00+00:00",
        "environment": {
            "label": "representative-staging-shared-redis",
            "github_run_id": run_id,
            "git_sha": "c" * 40,
        },
        "method": {
            "scenario": "authenticated-baseline",
            "candidate_windows_ms": windows,
            "baseline_window_ms": 0.0,
            "efficiency_metric": "runtime_records_per_sync_median",
            "latency_metric": "durability_wait_p95_ms_median",
            "efficiency_plateau_ratio": 0.95,
            "selection_rule": (
                "smallest eligible window reaching the configured fraction of "
                "the best observed median records-per-sync"
            ),
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


def _staging_report(run_id: str) -> dict:
    """构造一次真正通过 Live Staging Runner 的去敏证据。"""

    return {
        "schema_version": 1,
        "evidence_kind": "REPRESENTATIVE_STAGING_CALIBRATION_RUN_V1",
        "calibration_status": "REPRESENTATIVE_STAGING_PASS",
        "production_slo_authority": False,
        "production_default_updated": False,
        "generated_at": f"2026-09-03T12:0{run_id}:00+00:00",
        "environment": {
            "label": "representative-staging-shared-redis",
            "deployment_id": "staging-release-20260903-03",
            "git_sha": "c" * 40,
            "audit_group_commit_window_ms": 5.0,
            "endpoint_recorded": False,
            "audit_path_recorded": False,
        },
        "workload": {
            "request_count": 100,
            "logical_intent_request_counts": {
                "METRIC_QUERY": 40,
                "METADATA_LOOKUP": 20,
                "KNOWLEDGE": 20,
                "ANALYSIS": 20,
            },
            "response_contract_coverage": 1.0,
            "runtime_intent_match_coverage": 1.0,
            "tenant_subject_match_coverage": 1.0,
            "answer_validated_coverage": 1.0,
            "live_llm_call_coverage": 1.0,
            "tool_result_coverage": 1.0,
            "http_latency_ms": {"p95": 120.0 + int(run_id)},
            "runtime_latency_ms": {"p95": 100.0 + int(run_id)},
        },
        "audit": {
            "runtime_audit_coverage": 1.0,
            "persistence_receipt_coverage": 1.0,
            "runtime_records_per_sync": 4.0,
            "grouped_runtime_record_fraction": 1.0,
            "durability_wait_latency_ms": {"p95": 5.0 + int(run_id)},
            "batch_sync_latency_ms": {"p95": 1.0 + int(run_id)},
        },
        "tenancy": {
            "planned_tenant_count": 2,
            "planned_subject_count": 4,
            "cross_tenant_isolation_probe_passed": True,
        },
        "probes": {
            "timeout": {"passed": True},
            "admission_saturation": {"passed": True},
            "cross_tenant_isolation": {"passed": True},
        },
        "promotion": {
            "review_candidate_evidence": True,
            "automatic_production_promotion": False,
            "production_default_updated": False,
            "production_slo_authority": False,
            "explicit_human_approval_required": True,
        },
    }


def _review(staging_reports: list[dict]) -> dict:
    """运行完整集成评审。"""

    return build_representative_staging_promotion_review(
        [_calibration_report(value) for value in ("1", "2", "3")],
        staging_manifest_validation=_manifest_validation(),
        staging_calibration_reports=staging_reports,
        base_review_policy=_base_review_policy(),
        staging_review_policy=(
            load_representative_staging_promotion_review_policy(ROOT)
        ),
    )


def test_three_repeated_live_staging_runs_unlock_only_human_approval():
    """三次真实 Staging Run 全通过后，只能进入人工批准，绝不能自动改生产值。"""

    review = _review([_staging_report(value) for value in ("1", "2", "3")])

    assert review["review_status"] == "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL"
    staging = review["representative_staging_calibration"]
    assert staging["valid"] is True
    assert staging["evidence_count"] == 3
    assert staging["candidate_window_matches"] is True
    assert staging["workload_comparable"] is True
    assert staging["aggregate"]["runtime_audit_coverage_min"] == 1.0
    assert staging["aggregate"]["live_llm_call_coverage_min"] == 1.0
    assert review["decision"]["human_approval_required"] is True
    assert review["decision"]["automatic_production_promotion"] is False
    assert review["decision"]["production_default_updated"] is False
    assert review["decision"]["production_slo_authority"] is False


def test_manifest_alone_no_longer_unlocks_human_approval():
    """V1 Manifest 只是环境声明；没有真实重复运行时必须继续 Fail-Closed。"""

    review = _review([])

    assert review["review_status"] == "STAGING_CALIBRATION_EVIDENCE_REQUIRED"
    staging = review["representative_staging_calibration"]
    assert staging["valid"] is False
    assert "STAGING_EVIDENCE_COUNT_TOO_LOW" in staging["errors"]


def test_failed_live_coverage_or_probe_blocks_staging_promotion_review():
    """Live LLM、审计持久化或 Probe 任一失败，都不能伪装成可人工批准证据。"""

    reports = [_staging_report(value) for value in ("1", "2", "3")]
    reports[1]["workload"]["live_llm_call_coverage"] = 0.99
    reports[2]["audit"]["persistence_receipt_coverage"] = 0.98
    reports[2]["probes"]["timeout"]["passed"] = False

    review = _review(reports)

    assert review["review_status"] == "STAGING_CALIBRATION_EVIDENCE_REQUIRED"
    errors = set(review["representative_staging_calibration"]["errors"])
    assert errors >= {
        "STAGING_WORKLOAD_COVERAGE_INCOMPLETE",
        "STAGING_PERSISTENCE_RECEIPT_COVERAGE_INCOMPLETE",
        "STAGING_REQUIRED_PROBE_FAILED",
    }


def test_git_window_deployment_and_duplicate_run_identity_fail_closed():
    """Staging Run 必须来自同一部署/SHA/窗口，而且每份证据都必须是独立运行。"""

    reports = [_staging_report(value) for value in ("1", "2", "3")]
    reports[1]["environment"]["git_sha"] = "d" * 40
    reports[2]["environment"]["audit_group_commit_window_ms"] = 2.0
    reports[2]["environment"]["deployment_id"] = "other-deployment"
    reports[2]["generated_at"] = reports[0]["generated_at"]
    reports[2]["environment"]["deployment_id"] = reports[0]["environment"]["deployment_id"]
    # 先制造重复 Identity，再单独验证部署不一致。
    validation = validate_representative_staging_calibration_evidence(
        reports,
        manifest_validation=_manifest_validation(),
        candidate_window_ms=5.0,
        policy=load_representative_staging_promotion_review_policy(ROOT),
    )
    assert validation["valid"] is False
    assert "STAGING_EVIDENCE_DUPLICATE" in validation["errors"]
    assert "STAGING_GIT_SHA_MISMATCH" in validation["errors"]
    assert "STAGING_CANDIDATE_WINDOW_MISMATCH" in validation["errors"]

    other = deepcopy(reports)
    other[2] = _staging_report("4")
    other[2]["environment"]["deployment_id"] = "other-deployment"
    validation = validate_representative_staging_calibration_evidence(
        other,
        manifest_validation=_manifest_validation(),
        candidate_window_ms=5.0,
        policy=load_representative_staging_promotion_review_policy(ROOT),
    )
    assert "STAGING_DEPLOYMENT_MISMATCH" in validation["errors"]


def test_policy_v1_locks_three_runs_and_keeps_human_boundary():
    """Integration V1 固定三次真实运行、完整 Coverage 和人工审批边界。"""

    policy = load_representative_staging_promotion_review_policy(ROOT)
    assert policy["version"] == 1
    assert policy["review"]["minimum_representative_staging_evidence_files"] == 3
    assert policy["review"]["required_workload_coverage"] == 1.0
    assert policy["review"]["required_persistence_receipt_coverage"] == 1.0
    assert policy["promotion"]["explicit_human_approval_required"] is True
    assert policy["promotion"]["automatic_production_promotion"] is False
    assert policy["promotion"]["production_default_auto_update"] is False
    assert policy["promotion"]["production_slo_authority"] is False
