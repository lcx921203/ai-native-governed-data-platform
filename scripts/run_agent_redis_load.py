#!/usr/bin/env python3
"""运行 Agent Redis Admission Load Test，并生成可上传的 JSON Evidence。

支持直接执行：
    python scripts/run_agent_redis_load.py ...

Python 直接执行 scripts/*.py 时，默认只把 scripts/ 放进 sys.path，
仓库根目录不会自动成为可导入路径。因此这里先显式绑定 Repo Root，
再导入 acceptance.agent_slo。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 只加入当前脚本推导出的仓库根目录，不读取外部 PYTHONPATH。
root_text = str(ROOT)
if root_text not in sys.path:
    sys.path.insert(0, root_text)

from acceptance.agent_slo import run_profile


def main() -> int:
    """运行受治理 Load Profile；Correctness Under Load 失败时返回非 0。"""

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
