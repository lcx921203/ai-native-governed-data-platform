"""Audit Group Commit Window Calibration V1 的确定性契约测试。"""

from __future__ import annotations

from pathlib import Path

import yaml

from acceptance.agent_slo.audit_window_calibration import (
    build_calibration_summary,
)


ROOT = Path(__file__).resolve().parents[1]


def _observations() -> list[dict]:
    """构造三轮固定矩阵；1 ms 已进入最佳效率的 95% 平台。"""

    efficiency = {
        0.0: 1.0,
        0.5: 2.0,
        1.0: 4.9,
        2.0: 5.0,
        5.0: 5.1,
    }
    batches = {
        0.0: 100,
        0.5: 50,
        1.0: 21,
        2.0: 20,
        5.0: 20,
    }
    rows = []
    for window_ms in efficiency:
        for repeat in range(1, 4):
            rows.append(
                {
                    "window_ms": window_ms,
                    "repeat": repeat,
                    "whole_profile_correctness_pass": True,
                    "baseline_scenario_correctness_pass": True,
                    "runtime_audit_receipt_coverage": 1.0,
                    "attempts_per_second": 100.0,
                    "http_total_p95_ms": 20.0 + window_ms,
                    "durability_wait_p95_ms": 1.0 + window_ms,
                    "runtime_records_per_sync": efficiency[window_ms],
                    "grouped_runtime_record_fraction": (
                        0.0 if window_ms == 0.0 else 0.9
                    ),
                    "unique_sync_batches": batches[window_ms],
                }
            )
    return rows


def test_calibration_selects_smallest_window_on_efficiency_plateau():
    """达到最佳合并效率 95% 的最小窗口应成为 Lab Candidate。"""

    candidates = (0.0, 0.5, 1.0, 2.0, 5.0)
    report = build_calibration_summary(
        _observations(),
        candidate_windows_ms=candidates,
        repeats=3,
        environment_label="deterministic-test",
        plateau_ratio=0.95,
    )

    assert report["calibration_status"] == "LAB_CANDIDATE"
    assert report["selection"]["lab_candidate_window_ms"] == 1.0
    assert report["production_default_updated"] is False
    assert report["production_slo_authority"] is False
    one_ms = next(
        item
        for item in report["window_results"]
        if item["window_ms"] == 1.0
    )
    assert one_ms["sync_batch_reduction_vs_zero_ratio"] == 0.79
    assert one_ms["durability_wait_p95_delta_vs_zero_ms"] == 1.0


def test_incomplete_or_failed_candidate_is_not_eligible():
    """缺轮次必须失败；任何正确性失败的候选不得进入效率平台。"""

    rows = _observations()
    for row in rows:
        if row["window_ms"] == 1.0 and row["repeat"] == 2:
            row["whole_profile_correctness_pass"] = False

    report = build_calibration_summary(
        rows,
        candidate_windows_ms=(0.0, 0.5, 1.0, 2.0, 5.0),
        repeats=3,
        environment_label="deterministic-test",
        plateau_ratio=0.95,
    )
    one_ms = next(
        item
        for item in report["window_results"]
        if item["window_ms"] == 1.0
    )
    assert one_ms["eligible"] is False
    assert report["selection"]["lab_candidate_window_ms"] == 2.0


def test_v7_policy_and_manual_workflow_lock_the_calibration_matrix():
    """候选矩阵、重复次数和只读手工 Workflow 必须进入版本化契约。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )
    calibration = policy["audit_group_commit_window_calibration_v1"]
    assert policy["version"] == 7
    assert calibration["candidate_windows_ms"] == [0.0, 0.5, 1.0, 2.0, 5.0]
    assert calibration["minimum_repeated_runs"] == 3
    assert calibration["efficiency_plateau_ratio"] == 0.95
    assert calibration["production_default_auto_update"] is False

    workflow = (
        ROOT
        / ".github/workflows/agent-slo-calibration.yml"
    ).read_text(encoding="utf-8")
    assert "run_audit_group_commit_window_calibration.py" in workflow
    assert "audit-group-commit-window-calibration-v1.json" in workflow
    assert "audit-group-commit-window-calibration-v1-runs/*.json" in workflow


def test_e2e_runner_accepts_explicit_governed_window_without_changing_default():
    """单次 E2E CLI 可注入窗口，但生产 Audit Policy 默认值仍保持 1 ms。"""

    runner = (
        ROOT
        / "scripts/run_agent_api_e2e_load.py"
    ).read_text(encoding="utf-8")
    assert '"--audit-group-commit-window-ms"' in runner
    assert "audit_group_commit_window_ms" in runner

    audit_policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_audit_policy.yml"
        ).read_text(encoding="utf-8")
    )
    assert audit_policy["runtime"]["default_group_commit_window_ms"] == 1.0
