#!/usr/bin/env python3
"""运行 Representative Staging Promotion Review Integration V1。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.representative_staging_promotion_review import (
    run_representative_staging_promotion_review,
)


def main() -> int:
    """组合 Lab Calibration、Manifest 与真实 Staging Run；始终保持人工批准边界。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--calibration-evidence",
        action="append",
        required=True,
        help="Audit Group Commit Window Calibration V1 汇总 JSON，可重复传入。",
    )
    parser.add_argument(
        "--staging-manifest",
        required=True,
        help="Representative Staging Evidence Manifest V1。",
    )
    parser.add_argument(
        "--staging-evidence",
        action="append",
        required=True,
        help="Representative Staging Calibration Run V1 JSON，可重复传入。",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    review = run_representative_staging_promotion_review(
        ROOT,
        calibration_evidence_paths=args.calibration_evidence,
        staging_manifest_path=args.staging_manifest,
        staging_evidence_paths=args.staging_evidence,
        output_path=args.output,
    )
    print("Representative Staging Promotion Review:", args.output)
    print("Review status:", review["review_status"])
    print(
        "Candidate window ms:",
        review["candidate_consensus"]["window_ms"],
    )
    print(
        "Representative staging run evidence valid:",
        review["representative_staging_calibration"]["valid"],
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
