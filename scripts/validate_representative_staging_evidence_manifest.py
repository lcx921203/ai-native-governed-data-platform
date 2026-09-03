#!/usr/bin/env python3
"""验证 Representative Staging Evidence Manifest V1。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.staging_evidence_manifest import (
    validate_staging_evidence_file,
    write_validation_report,
)


def main() -> int:
    """执行 Fail-closed 清单校验；失败时仍输出去敏后的错误代码。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = validate_staging_evidence_file(
        ROOT,
        manifest_path=args.manifest,
    )
    write_validation_report(report, args.output)
    print("Representative Staging Evidence Manifest:", args.manifest)
    print("Validation report:", args.output)
    print("Valid:", str(report["valid"]).lower())
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
