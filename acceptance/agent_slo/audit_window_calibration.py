"""Audit Group Commit Window Calibration V1（审计组提交窗口校准）。

该模块只比较同一 Environment、同一 Profile 下的受控实验室 Evidence。
它可以产生 ``LAB_CANDIDATE``，但不能修改生产默认值或晋升生产 SLO。
"""

from __future__ import annotations

import json
import os
import platform
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Iterable

import yaml

from .api_e2e_load import run_api_e2e_profile


BASELINE_SCENARIO = "authenticated-baseline"


def _policy(project_root: Path) -> dict:
    """读取版本化校准策略，避免 CLI 与治理契约各自维护候选窗口。"""

    payload = yaml.safe_load(
        (
            project_root
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )
    return payload["audit_group_commit_window_calibration_v1"]


def _baseline_observation(
    report: dict,
    *,
    window_ms: float,
    repeat: int,
) -> dict:
    """只抽取无刻意限流的 Baseline Scenario，避免 429 场景污染延迟比较。"""

    scenario = next(
        (
            item
            for item in report["scenario_results"]
            if item["scenario"]["name"] == BASELINE_SCENARIO
        ),
        None,
    )
    if scenario is None:
        raise ValueError(
            f"Calibration evidence is missing {BASELINE_SCENARIO!r}."
        )

    audit = scenario["latency_breakdown"]["audit_persistence"]
    return {
        "window_ms": float(window_ms),
        "repeat": int(repeat),
        "whole_profile_correctness_pass": bool(report["correctness_pass"]),
        "baseline_scenario_correctness_pass": bool(
            scenario["result"]["correctness_pass"]
        ),
        "runtime_audit_receipt_coverage": float(
            audit["runtime_audit_receipt_coverage"]
        ),
        "attempts_per_second": float(
            scenario["result"]["attempts_per_second"]
        ),
        "http_total_p95_ms": float(
            scenario["http_total_latency_ms"]["p95"]
        ),
        "durability_wait_p95_ms": float(
            audit["durability_wait_latency_ms"]["p95"]
        ),
        "runtime_records_per_sync": float(
            audit["runtime_records_per_sync"]
        ),
        "grouped_runtime_record_fraction": float(
            audit["grouped_runtime_record_fraction"]
        ),
        "unique_sync_batches": int(audit["unique_sync_batches"]),
    }


def build_calibration_summary(
    observations: Iterable[dict],
    *,
    candidate_windows_ms: Iterable[float],
    repeats: int,
    environment_label: str,
    plateau_ratio: float,
) -> dict:
    """聚合重复实验，并选出达到效率平台的最小实验室候选窗口。"""

    candidates = tuple(float(value) for value in candidate_windows_ms)
    rows = list(observations)
    grouped: dict[float, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[float(row["window_ms"])].append(row)

    summaries = []
    for window_ms in candidates:
        runs = grouped.get(window_ms, [])
        if len(runs) != repeats:
            raise ValueError(
                f"Window {window_ms} ms requires {repeats} runs; observed {len(runs)}."
            )

        correctness_pass = all(
            row["whole_profile_correctness_pass"]
            and row["baseline_scenario_correctness_pass"]
            for row in runs
        )
        coverage_min = min(
            row["runtime_audit_receipt_coverage"]
            for row in runs
        )
        summaries.append(
            {
                "window_ms": window_ms,
                "run_count": len(runs),
                "correctness_pass": correctness_pass,
                "runtime_audit_receipt_coverage_min": coverage_min,
                "eligible": correctness_pass and coverage_min == 1.0,
                "runtime_records_per_sync_median": round(
                    median(
                        row["runtime_records_per_sync"]
                        for row in runs
                    ),
                    6,
                ),
                "grouped_runtime_record_fraction_median": round(
                    median(
                        row["grouped_runtime_record_fraction"]
                        for row in runs
                    ),
                    6,
                ),
                "unique_sync_batches_median": round(
                    median(
                        row["unique_sync_batches"]
                        for row in runs
                    ),
                    6,
                ),
                "durability_wait_p95_ms_median": round(
                    median(
                        row["durability_wait_p95_ms"]
                        for row in runs
                    ),
                    6,
                ),
                "durability_wait_p95_ms_worst": max(
                    row["durability_wait_p95_ms"]
                    for row in runs
                ),
                "http_total_p95_ms_median": round(
                    median(
                        row["http_total_p95_ms"]
                        for row in runs
                    ),
                    6,
                ),
                "attempts_per_second_median": round(
                    median(
                        row["attempts_per_second"]
                        for row in runs
                    ),
                    6,
                ),
            }
        )

    eligible = [item for item in summaries if item["eligible"]]
    best_efficiency = max(
        (
            item["runtime_records_per_sync_median"]
            for item in eligible
        ),
        default=0.0,
    )
    plateau_threshold = best_efficiency * float(plateau_ratio)
    plateau = [
        item
        for item in eligible
        if item["runtime_records_per_sync_median"] >= plateau_threshold
    ]
    selected = min(
        plateau,
        key=lambda item: item["window_ms"],
        default=None,
    )

    baseline = next(
        item
        for item in summaries
        if item["window_ms"] == 0.0
    )
    for item in summaries:
        baseline_batches = float(
            baseline["unique_sync_batches_median"]
        )
        item["sync_batch_reduction_vs_zero_ratio"] = round(
            0.0
            if baseline_batches == 0.0
            else 1.0
            - float(item["unique_sync_batches_median"])
            / baseline_batches,
            6,
        )
        item["durability_wait_p95_delta_vs_zero_ms"] = round(
            float(item["durability_wait_p95_ms_median"])
            - float(baseline["durability_wait_p95_ms_median"]),
            6,
        )

    return {
        "schema_version": 1,
        "evidence_kind": "AUDIT_GROUP_COMMIT_WINDOW_CALIBRATION_V1",
        "calibration_status": (
            "LAB_CANDIDATE" if selected else "NO_VALID_LAB_CANDIDATE"
        ),
        "production_slo_authority": False,
        "production_default_updated": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "label": environment_label,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
            "git_sha": os.getenv("GITHUB_SHA", ""),
        },
        "method": {
            "scenario": BASELINE_SCENARIO,
            "candidate_windows_ms": list(candidates),
            "repeats_per_window": repeats,
            "baseline_window_ms": 0.0,
            "efficiency_metric": "runtime_records_per_sync_median",
            "latency_metric": "durability_wait_p95_ms_median",
            "efficiency_plateau_ratio": float(plateau_ratio),
            "selection_rule": (
                "smallest eligible window reaching the configured fraction of "
                "the best observed median records-per-sync"
            ),
        },
        "window_results": summaries,
        "selection": {
            "lab_candidate_window_ms": (
                selected["window_ms"] if selected else None
            ),
            "best_observed_records_per_sync_median": best_efficiency,
            "efficiency_plateau_threshold": round(
                plateau_threshold,
                6,
            ),
            "automatic_production_promotion": False,
            "reason": (
                "GitHub-hosted lab evidence selects only a review candidate; "
                "representative staging repetition and human approval remain required."
            ),
        },
    }


def run_audit_window_calibration(
    project_root: Path | str,
    *,
    output_path: Path | str,
    environment_label: str,
    repeats: int | None = None,
) -> dict:
    """顺序运行固定候选矩阵，保存每次 E2E Evidence 与最终汇总。"""

    root = Path(project_root).resolve()
    output = Path(output_path)
    policy = _policy(root)
    minimum_repeats = int(policy["minimum_repeated_runs"])
    maximum_repeats = int(policy["maximum_repeated_runs"])
    repeat_count = minimum_repeats if repeats is None else int(repeats)
    if not minimum_repeats <= repeat_count <= maximum_repeats:
        raise ValueError(
            "Calibration repeats are outside governed bounds."
        )

    candidates = tuple(
        float(value)
        for value in policy["candidate_windows_ms"]
    )
    run_dir = output.parent / f"{output.stem}-runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    observations = []

    for window_ms in candidates:
        window_label = str(window_ms).replace(".", "p")
        for repeat in range(1, repeat_count + 1):
            run_path = (
                run_dir
                / f"window-{window_label}ms-run-{repeat}.json"
            )
            report = run_api_e2e_profile(
                root,
                profile="lab",
                output_path=run_path,
                environment_label=environment_label,
                audit_group_commit_window_ms=window_ms,
            )
            observations.append(
                _baseline_observation(
                    report,
                    window_ms=window_ms,
                    repeat=repeat,
                )
            )

    summary = build_calibration_summary(
        observations,
        candidate_windows_ms=candidates,
        repeats=repeat_count,
        environment_label=environment_label,
        plateau_ratio=float(policy["efficiency_plateau_ratio"]),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return summary
