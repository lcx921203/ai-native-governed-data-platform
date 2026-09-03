#!/usr/bin/env python3
"""运行 Audit Group Commit Window Calibration V1。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.audit_window_calibration import (
    run_audit_window_calibration,
)


def main() -> int:
    """执行受治理的固定窗口矩阵；无有效候选时返回非 0。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--environment-label",
        default="local-audit-window-lab",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=None,
        help="每个候选窗口的重复次数；默认读取治理策略。",
    )
    args = parser.parse_args()

    report = run_audit_window_calibration(
        ROOT,
        output_path=Path(args.output),
        environment_label=args.environment_label,
        repeats=args.repeats,
    )
    print("Audit Group Commit Window Calibration:", args.output)
    print("Calibration status:", report["calibration_status"])
    print(
        "Lab candidate window ms:",
        report["selection"]["lab_candidate_window_ms"],
    )
    print("Production default updated: false")
    return (
        0
        if report["calibration_status"] == "LAB_CANDIDATE"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
