#!/usr/bin/env python3
"""运行 Representative Staging Calibration Runner V1。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.representative_staging_calibration import (
    run_representative_staging_calibration,
)


def main() -> int:
    """执行真实 Staging 混合负载；任何机械门禁失败都返回非 0。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = run_representative_staging_calibration(
        ROOT,
        manifest_path=Path(args.manifest),
        plan_path=Path(args.plan),
        output_path=Path(args.output),
    )
    print("Representative Staging Calibration:", args.output)
    print("Calibration status:", report["calibration_status"])
    print("Request count:", report["workload"]["request_count"])
    print(
        "Runtime audit coverage:",
        report["audit"]["runtime_audit_coverage"],
    )
    print(
        "Audit persistence receipt coverage:",
        report["audit"]["persistence_receipt_coverage"],
    )
    print("Production default updated: false")
    return 0 if report["calibration_status"] == "REPRESENTATIVE_STAGING_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
