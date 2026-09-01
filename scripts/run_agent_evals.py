#!/usr/bin/env python3
"""运行 Agent Static Regression Eval。

用法：
    python scripts/run_agent_evals.py

只跑某些 suite：
    python scripts/run_agent_evals.py semantic_queries analysis_queries

输出 JSON：
    python scripts/run_agent_evals.py --json artifacts/agent-eval.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.evals import (  # noqa: E402
    GovernedAgentEvalRunner,
    render_text_report,
    write_json_report,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("suites", nargs="*")
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args()

    runner = GovernedAgentEvalRunner(ROOT)
    report = runner.run(args.suites or None)

    print(render_text_report(report))
    if args.json_path:
        write_json_report(report, ROOT / args.json_path)

    try:
        runner.assert_gate(report)
    except AssertionError as exc:
        print(f"\nREGRESSION_GATE=FAIL: {exc}", file=sys.stderr)
        return 1

    print("\nREGRESSION_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
