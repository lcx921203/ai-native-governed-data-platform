#!/usr/bin/env python3
"""运行 Agent Redis Admission Load Test，并生成可上传的 JSON Evidence。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from acceptance.agent_slo import run_profile

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=("ci-smoke", "lab"),
        default="ci-smoke",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="JSON evidence output path; Redis URL is never serialized.",
    )
    parser.add_argument(
        "--environment-label",
        default="local-lab",
    )
    args = parser.parse_args()

    report = asyncio.run(
        run_profile(
            ROOT,
            profile=args.profile,
            output_path=Path(args.output),
            environment_label=args.environment_label,
        )
    )

    print("Agent Redis load evidence:", args.output)
    for item in report["scenario_results"]:
        result = item["result"]
        latency = item["latency_ms"]["admission"]
        print(
            f"- {item['scenario']['name']}: "
            f"attempts={result['attempts']} "
            f"admitted={result['admitted']} "
            f"rejected={result['rejected']} "
            f"p95_ms={latency['p95']} "
            f"correctness={result['correctness_pass']}"
        )

    print(
        "Production SLO status:",
        report["promotion"]["production_slo_status"],
    )
    return 0 if report["correctness_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
