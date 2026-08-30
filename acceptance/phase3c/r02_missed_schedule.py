#!/usr/bin/env python
"""R02 Definition Runtime: missed schedule / no-run recovery sensor evaluation.

This uses a fully persistent temporary Dagster instance because build_sensor_context
rejects DagsterInstance.ephemeral() when an instance is supplied. No job is launched;
the script proves the real SensorDefinition emits the bounded recovery RunRequest.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import dagster as dg

from commerce_dagster import sensors as sensor_module
from commerce_dagster.recovery_state_current import (
    AUTO_RECOVERY_TAG_VALUE,
    RECOVERY_ATTEMPT_TAG,
    RECOVERY_REASON_TAG,
    RECOVERY_TAG,
    collect_partition_recovery_state,
)
from commerce_dagster.sensors import shopify_daily_recovery_sensor


FIXED_NOW = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
EXPECTED_PARTITION = "2026-08-05"
EXPECTED_RUN_KEY = "shopify-daily-recovery:2026-08-05:attempt-1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    with tempfile.TemporaryDirectory(prefix="commerce-r02-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            before = collect_partition_recovery_state(
                instance,
                partition_key=EXPECTED_PARTITION,
                freshness_overdue=True,
                infrastructure_healthy=True,
                missed_schedule_eligible=True,
            )

            with dg.build_sensor_context(instance=instance) as context:
                with (
                    patch.object(sensor_module, "utc_now", return_value=FIXED_NOW),
                    patch.object(
                        sensor_module,
                        "docker_compose_services_running",
                        return_value=True,
                    ),
                ):
                    result = shopify_daily_recovery_sensor(context)

            is_run_request = isinstance(result, dg.RunRequest)
            conditions = {
                "no_run_exists_before_sensor": not before.run_ids,
                "exact_partition_is_incomplete": bool(before.missing_mart_asset_keys),
                "sensor_emits_one_run_request": is_run_request,
                "partition_key_is_2026_08_05": (
                    is_run_request and result.partition_key == EXPECTED_PARTITION
                ),
                "stable_run_key": (
                    is_run_request and result.run_key == EXPECTED_RUN_KEY
                ),
                "recovery_origin_tag": (
                    is_run_request
                    and result.tags.get("commerce/automation") == "recovery-sensor"
                ),
                "auto_recovery_tag": (
                    is_run_request
                    and result.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
                ),
                "attempt_is_one": (
                    is_run_request and result.tags.get(RECOVERY_ATTEMPT_TAG) == "1"
                ),
                "reason_is_missed_schedule": (
                    is_run_request
                    and result.tags.get(RECOVERY_REASON_TAG)
                    == "missed_schedule_or_no_run"
                ),
            }

            payload = {
                "scenario": "R02-A",
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "expected_partition_key": EXPECTED_PARTITION,
                "run_ids_before_sensor": list(before.run_ids),
                "missing_marts_before_sensor": list(before.missing_mart_asset_keys),
                "result_type": type(result).__name__,
                "run_key": result.run_key if is_run_request else None,
                "partition_key": result.partition_key if is_run_request else None,
                "tags": dict(result.tags) if is_run_request else {},
                "conditions": conditions,
                "result": "PASS" if all(conditions.values()) else "FAIL",
                "evidence_level": "C1-sensor-definition-runtime",
                "does_not_prove": [
                    "Dagster daemon committed the sensor tick",
                    "run_key dedup prevented a second real run",
                    "the recovery run materialized 9/9 marts",
                ],
            }

            rendered = json.dumps(payload, indent=2, sort_keys=True)
            print(rendered)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + "\n", encoding="utf-8")

            return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
