#!/usr/bin/env python3
"""生成 Agent SLO Human Approval Record V1。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.human_approval_record import (
    canonical_json_sha256,
    run_human_approval_record,
)


def main() -> int:
    """只生成审批证据；绝不修改生产 Guardrail。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--promotion-review",
        required=True,
        help="已达到 STAGING_EVIDENCE_READY_FOR_HUMAN_APPROVAL 的 Review JSON。",
    )
    parser.add_argument(
        "--decision",
        default=None,
        help="受信任审批渠道生成的 Human Approval Decision V1 YAML。",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Human Approval Record V1 输出 JSON。",
    )
    parser.add_argument(
        "--print-review-fingerprint",
        action="store_true",
        help="只打印 Promotion Review Canonical SHA-256，便于填写 Decision 模板。",
    )
    args = parser.parse_args()

    if args.print_review_fingerprint:
        review = json.loads(
            Path(args.promotion_review).read_text(encoding="utf-8")
        )
        print(canonical_json_sha256(review))
        return 0

    if not args.decision or not args.output:
        parser.error(
            "--decision and --output are required unless --print-review-fingerprint is used"
        )

    record = run_human_approval_record(
        ROOT,
        promotion_review_path=args.promotion_review,
        decision_path=args.decision,
        output_path=args.output,
    )
    print("Agent SLO Human Approval Record:", args.output)
    print("Approval status:", record["approval_status"])
    print(
        "Candidate window ms:",
        record["review"]["candidate_window_ms"],
    )
    print(
        "Versioned change authorized:",
        record["authorization"]["versioned_change_authorized"],
    )
    print("Automatic production application: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
