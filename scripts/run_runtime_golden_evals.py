#!/usr/bin/env python3
"""运行 Synthetic Runtime Golden Eval。

    python scripts/run_runtime_golden_evals.py

输出 JSON：
    python scripts/run_runtime_golden_evals.py --json artifacts/runtime-golden.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.evals.runtime_golden import GovernedRuntimeGoldenEvalRunner  # noqa: E402
from agent.evals.runtime_report import (  # noqa: E402
    render_runtime_golden_report,
    write_runtime_golden_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    runner = GovernedRuntimeGoldenEvalRunner(ROOT)
    report = runner.run()
    print(render_runtime_golden_report(report))

    if args.json_path:
        write_runtime_golden_json(report, ROOT / args.json_path)

    try:
        runner.assert_gate(report)
    except AssertionError as exc:
        print(f"\nRUNTIME_GOLDEN_GATE=FAIL: {exc}", file=sys.stderr)
        return 1

    print("\nRUNTIME_GOLDEN_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
