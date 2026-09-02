#!/usr/bin/env python3
"""运行 Authenticated Agent API E2E Load，并生成 JSON Evidence。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Python 直接执行 scripts/*.py 时需要显式加入 Repo Root。
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo.api_e2e_load import run_api_e2e_profile


def main() -> int:
    """CLI 入口；Correctness Under Load 失败返回非 0。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=("ci-smoke", "lab"),
        default="ci-smoke",
    )
    parser.add_argument(
        "--output",
        required=True,
    )
    parser.add_argument(
        "--environment-label",
        default="local-api-e2e",
    )
    args = parser.parse_args()

    report = run_api_e2e_profile(
        ROOT,
        profile=args.profile,
        output_path=Path(args.output),
        environment_label=args.environment_label,
    )

    print("Agent API E2E load evidence:", args.output)
    for item in report["scenario_results"]:
        result = item["result"]
        latency = item["http_total_latency_ms"]
        breakdown = item["latency_breakdown"]
        stage_map = breakdown["runtime_stage_latency_ms"]
        ranked = sorted(
            (
                (stage, values.get("p95"))
                for stage, values in stage_map.items()
                if values.get("p95") is not None
            ),
            key=lambda pair: float(pair[1]),
            reverse=True,
        )
        top_stage = (
            f"{ranked[0][0]}:{ranked[0][1]}ms"
            if ranked
            else "none"
        )
        print(
            f"- {item['scenario']['name']}: "
            f"attempts={result['attempts']} "
            f"status={result['status_counts']} "
            f"rejections={result['rejection_counts']} "
            f"http_p95_ms={latency['p95']} "
            f"runtime_p95_ms={breakdown['runtime_total_latency_ms']['p95']} "
            f"outside_runtime_p95_ms={breakdown['http_outside_runtime_latency_ms']['p95']} "
            f"top_runtime_stage={top_stage} "
            f"stage_coverage={breakdown['stage_timing_coverage']} "
            f"correctness={result['correctness_pass']}"
        )

    print(
        "Production SLO status:",
        report["promotion"]["production_slo_status"],
    )
    return 0 if report["correctness_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
