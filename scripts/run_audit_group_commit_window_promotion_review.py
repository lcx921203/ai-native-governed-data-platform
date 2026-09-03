#!/usr/bin/env python3
"""汇总多份 Calibration V1 证据，生成 Audit Window 人工晋升评审。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.audit_window_promotion_review import (
    run_promotion_review,
)


def main() -> int:
    """执行 Fail-closed（失败关闭）的证据汇总；不修改任何生产配置。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--evidence",
        action="append",
        required=True,
        help="Calibration V1 汇总 JSON；至少传入治理策略要求的数量。",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--staging-manifest",
        default=None,
        help="可选的 Representative Staging Evidence Manifest V1。",
    )
    args = parser.parse_args()

    review = run_promotion_review(
        ROOT,
        evidence_paths=args.evidence,
        output_path=args.output,
        staging_manifest_path=args.staging_manifest,
    )
    print("Audit Group Commit Window Promotion Review:", args.output)
    print("Review status:", review["review_status"])
    print(
        "Candidate window ms:",
        review["candidate_consensus"]["window_ms"],
    )
    print("Automatic production promotion: false")
    return (
        0
        if review["review_status"]
        in {
            "LAB_REVIEW_ONLY",
            "STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL",
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
