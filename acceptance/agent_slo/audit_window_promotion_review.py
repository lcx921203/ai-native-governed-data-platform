"""Audit Group Commit Window Promotion Review V1（审计窗口晋升评审）。

该模块消费多份 Calibration V1 汇总证据，检查候选窗口是否稳定、实验方法是否
可比较以及运行身份是否重复。输出只能进入人工评审，绝不直接修改生产默认值。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import yaml

from .staging_evidence_manifest import validate_staging_evidence_file

CALIBRATION_KIND = "AUDIT_GROUP_COMMIT_WINDOW_CALIBRATION_V1"
REVIEW_KIND = "AUDIT_GROUP_COMMIT_WINDOW_PROMOTION_REVIEW_V1"


def _policy(project_root: Path) -> dict:
    """读取晋升评审策略，使证据门槛保持版本化且可测试。"""

    payload = yaml.safe_load(
        (
            project_root
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )
    return payload["audit_group_commit_window_promotion_review_v1"]


def _method_signature(report: Mapping) -> tuple:
    """提取决定实验可比性的字段，避免混合不同候选矩阵或选择规则。"""

    method = report.get("method") or {}
    return (
        tuple(float(value) for value in method.get("candidate_windows_ms", ())),
        float(method.get("baseline_window_ms", -1.0)),
        str(method.get("scenario", "")),
        str(method.get("efficiency_metric", "")),
        str(method.get("latency_metric", "")),
        float(method.get("efficiency_plateau_ratio", -1.0)),
        str(method.get("selection_rule", "")),
    )


def _evidence_identity(report: Mapping) -> str:
    """优先使用 GitHub Run ID；非 GitHub 环境使用生成时间与环境标签组合。"""

    environment = report.get("environment") or {}
    run_id = str(environment.get("github_run_id") or "").strip()
    if run_id:
        return f"github:{run_id}"
    generated_at = str(report.get("generated_at") or "").strip()
    label = str(environment.get("label") or "").strip()
    return f"local:{label}:{generated_at}"


def _selected_window_result(report: Mapping, window_ms: float) -> Mapping:
    """返回本次证据中候选窗口的聚合行；缺失时按不可信证据失败。"""

    for row in report.get("window_results") or ():
        if float(row.get("window_ms", -1.0)) == float(window_ms):
            return row
    raise ValueError(
        f"Calibration evidence is missing selected window {window_ms} ms."
    )


def build_promotion_review(
    reports: Iterable[Mapping],
    *,
    minimum_evidence_count: int,
    representative_staging_label_prefix: str,
    require_same_git_sha: bool,
    staging_manifest_validation: Mapping | None = None,
) -> dict:
    """汇总多次校准证据，并生成不可自动晋升的人工评审结论。"""

    items = list(reports)
    if len(items) < int(minimum_evidence_count):
        raise ValueError(
            "Promotion review has fewer calibration evidence files than required."
        )

    for report in items:
        if report.get("evidence_kind") != CALIBRATION_KIND:
            raise ValueError("Promotion review received an unsupported evidence kind.")
        if int(report.get("schema_version", 0)) != 1:
            raise ValueError("Promotion review requires Calibration V1 evidence.")
        if report.get("production_slo_authority") is not False:
            raise ValueError("Calibration evidence must not claim production authority.")
        if report.get("production_default_updated") is not False:
            raise ValueError("Calibration evidence must not update the production default.")

    identities = [_evidence_identity(report) for report in items]
    if any(identity.endswith(":") for identity in identities):
        raise ValueError("Calibration evidence is missing a stable run identity.")
    if len(set(identities)) != len(identities):
        raise ValueError("Promotion review rejects duplicate calibration evidence.")

    method_signatures = {_method_signature(report) for report in items}
    if len(method_signatures) != 1:
        raise ValueError("Calibration evidence methods are not comparable.")

    git_shas = {
        str((report.get("environment") or {}).get("git_sha") or "").strip()
        for report in items
    }
    if require_same_git_sha and ("" in git_shas or len(git_shas) != 1):
        raise ValueError("Promotion review requires one non-empty shared Git SHA.")

    selected_windows = [
        (report.get("selection") or {}).get("lab_candidate_window_ms")
        for report in items
    ]
    valid_candidate_runs = [
        report.get("calibration_status") == "LAB_CANDIDATE"
        and selected_window is not None
        for report, selected_window in zip(items, selected_windows)
    ]
    stable_candidate = (
        all(valid_candidate_runs)
        and len({float(value) for value in selected_windows}) == 1
    )
    candidate_window_ms = (
        float(selected_windows[0]) if stable_candidate else None
    )

    environment_labels = [
        str((report.get("environment") or {}).get("label") or "")
        for report in items
    ]
    staging_label_claimed = all(
        label.startswith(representative_staging_label_prefix)
        for label in environment_labels
    )

    selected_rows = (
        [
            _selected_window_result(report, candidate_window_ms)
            for report in items
        ]
        if candidate_window_ms is not None
        else []
    )
    selected_rows_eligible = bool(selected_rows) and all(
        row.get("eligible") is True
        and float(row.get("runtime_audit_receipt_coverage_min", 0.0)) == 1.0
        for row in selected_rows
    )

    manifest_validation = (
        staging_manifest_validation
        if isinstance(staging_manifest_validation, Mapping)
        else {}
    )
    manifest_valid = manifest_validation.get("valid") is True
    manifest_environment_matches = (
        manifest_valid
        and len(set(environment_labels)) == 1
        and manifest_validation.get("environment_label") == environment_labels[0]
    )
    shared_git_sha = next(iter(git_shas)) if len(git_shas) == 1 else None
    manifest_git_sha_matches = (
        manifest_valid
        and shared_git_sha is not None
        and manifest_validation.get("git_sha") == shared_git_sha
    )
    raw_manifest_window = manifest_validation.get(
        "audit_group_commit_window_ms"
    )
    try:
        manifest_window = (
            None
            if isinstance(raw_manifest_window, bool)
            else float(raw_manifest_window)
        )
    except (TypeError, ValueError):
        manifest_window = None
    manifest_window_matches = (
        manifest_valid
        and candidate_window_ms is not None
        and manifest_window == candidate_window_ms
    )
    representative_staging = bool(
        staging_label_claimed
        and manifest_valid
        and manifest_environment_matches
        and manifest_git_sha_matches
        and manifest_window_matches
    )

    if not stable_candidate or not selected_rows_eligible:
        review_status = "NOT_READY"
    elif staging_label_claimed and not representative_staging:
        review_status = "STAGING_MANIFEST_REQUIRED"
    elif representative_staging:
        review_status = "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL"
    else:
        review_status = "LAB_REVIEW_ONLY"

    aggregate = None
    if selected_rows:
        aggregate = {
            "runtime_records_per_sync_median_of_medians": round(
                median(
                    float(row["runtime_records_per_sync_median"])
                    for row in selected_rows
                ),
                6,
            ),
            "durability_wait_p95_ms_median_of_medians": round(
                median(
                    float(row["durability_wait_p95_ms_median"])
                    for row in selected_rows
                ),
                6,
            ),
            "durability_wait_p95_ms_worst_across_runs": round(
                max(
                    float(row["durability_wait_p95_ms_worst"])
                    for row in selected_rows
                ),
                6,
            ),
            "runtime_audit_receipt_coverage_min": min(
                float(row["runtime_audit_receipt_coverage_min"])
                for row in selected_rows
            ),
        }

    return {
        "schema_version": 1,
        "evidence_kind": REVIEW_KIND,
        "review_status": review_status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_count": len(items),
        "evidence_identities": identities,
        "environment_labels": environment_labels,
        "git_sha": shared_git_sha,
        "method_comparable": True,
        "representative_staging": representative_staging,
        "staging_manifest": {
            "provided": bool(staging_manifest_validation),
            "valid": manifest_valid,
            "environment_matches": manifest_environment_matches,
            "git_sha_matches": manifest_git_sha_matches,
            "candidate_window_matches": manifest_window_matches,
            "errors": list(manifest_validation.get("errors") or ()),
        },
        "candidate_consensus": {
            "stable": stable_candidate,
            "window_ms": candidate_window_ms,
            "supporting_evidence_count": (
                len(items) if stable_candidate else 0
            ),
        },
        "selected_window_aggregate": aggregate,
        "decision": {
            "human_approval_required": True,
            "automatic_production_promotion": False,
            "production_default_updated": False,
            "production_slo_authority": False,
            "reason": (
                "Representative staging evidence passed mechanical review; "
                "explicit human approval is still required."
                if review_status
                == "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL"
                else (
                    "Representative staging label requires a valid manifest "
                    "matching environment, Git SHA, and candidate window."
                    if review_status == "STAGING_MANIFEST_REQUIRED"
                    else "Lab evidence may support engineering review only; "
                    "it cannot promote production settings."
                )
            ),
        },
    }


def run_promotion_review(
    project_root: Path | str,
    *,
    evidence_paths: Iterable[Path | str],
    output_path: Path | str,
    staging_manifest_path: Path | str | None = None,
) -> dict:
    """读取校准汇总文件、生成评审证据并保存为 JSON。"""

    root = Path(project_root).resolve()
    policy = _policy(root)
    paths = [Path(path) for path in evidence_paths]
    reports = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in paths
    ]
    manifest_validation = (
        validate_staging_evidence_file(
            root,
            manifest_path=staging_manifest_path,
        )
        if staging_manifest_path is not None
        else None
    )
    review = build_promotion_review(
        reports,
        minimum_evidence_count=int(policy["minimum_calibration_evidence_files"]),
        representative_staging_label_prefix=str(
            policy["representative_staging_label_prefix"]
        ),
        require_same_git_sha=bool(policy["require_same_git_sha"]),
        staging_manifest_validation=manifest_validation,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return review
