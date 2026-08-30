#!/usr/bin/env python3
"""R01-A: evaluate the real resolved Dagster schedule at a fixed historical tick.

This is Definition Runtime evidence, not Daemon evidence. It proves that the loaded
Dagster ScheduleDefinition maps 2026-08-06 00:15 UTC to partition 2026-08-05 and
attaches the expected schedule-origin tags/run key.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DAGSTER_CODE = ROOT / "orchestration" / "dagster"
sys.path.insert(0, str(DAGSTER_CODE))

SCHEDULE_NAME = "shopify_daily_partition_schedule"
FIXED_TICK = datetime(2026, 8, 6, 0, 15, tzinfo=timezone.utc)
EXPECTED_PARTITION = "2026-08-05"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import dagster as dg
        from commerce_dagster.definitions import defs
    except ImportError as exc:
        print(f"Dagster runtime is not installed: {exc}", file=sys.stderr)
        return 2

    schedule_def = defs.get_schedule_def(SCHEDULE_NAME)
    context = dg.build_schedule_context(scheduled_execution_time=FIXED_TICK)
    evaluation = schedule_def.evaluate_tick(context)
    requests = list(evaluation.run_requests or [])

    if len(requests) != 1:
        report = {
            "scenario": "R01-A",
            "result": "FAIL",
            "reason": f"expected exactly 1 RunRequest, got {len(requests)}",
        }
    else:
        request = requests[0]
        actual_partition = request.tags.get("dagster/partition")
        conditions = {
            "one_run_request": True,
            "partition_key_is_2026_08_05": actual_partition == EXPECTED_PARTITION,
            "run_key_is_partition_key": request.run_key == EXPECTED_PARTITION,
            "schedule_origin_tag_present": (
                request.tags.get("commerce/automation") == "daily-schedule"
            ),
        }
        report = {
            "scenario": "R01-A",
            "result": "PASS" if all(conditions.values()) else "FAIL",
            "scheduled_execution_time_utc": FIXED_TICK.isoformat().replace("+00:00", "Z"),
            "expected_partition_key": EXPECTED_PARTITION,
            "actual_partition_key": actual_partition,
            "run_key": request.run_key,
            "run_tags": dict(sorted(request.tags.items())),
            "pass_conditions": conditions,
        }

    rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
