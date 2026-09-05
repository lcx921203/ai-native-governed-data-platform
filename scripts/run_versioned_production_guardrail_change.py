#!/usr/bin/env python3
"""生成 Versioned Production Guardrail Change V1 包。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.versioned_production_guardrail_change import (
    run_versioned_production_guardrail_change,
)


def main() -> int:
    """生成待人工应用的版本化变更包；绝不覆盖仓库生产策略。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--promotion-review",
        required=True,
        help="Representative Staging Promotion Review JSON。",
    )
    parser.add_argument(
        "--approval-record",
        required=True,
        help="AGENT_SLO_HUMAN_APPROVAL_RECORD_V1 JSON。",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="输出 Change Record 与 proposed/ 目标文件的目录。",
    )
    args = parser.parse_args()

    record = run_versioned_production_guardrail_change(
        ROOT,
        promotion_review_path=args.promotion_review,
        approval_record_path=args.approval_record,
        output_dir=args.output_dir,
    )
    print("Versioned Production Guardrail Change:", args.output_dir)
    print("Change status:", record["change_status"])
    print("From value:", record["target"]["from_value"])
    print("To value:", record["target"]["to_value"])
    print("Manual application required: true")
    print("Repository target overwritten: false")
    print("Automatic production application: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
