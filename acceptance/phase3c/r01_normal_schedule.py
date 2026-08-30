#!/usr/bin/env python3
"""R01 Normal Schedule runtime evidence collector.

This script does not launch a run. It inspects the persistent Dagster instance after a
real schedule-launched run and fails closed unless all nine consumer marts were
materialized for the exact partition by the same schedule run before the 01:00 UTC
consumer deadline.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "orchestration" / "dagster" / "commerce_dagster"
sys.path.insert(0, str(PKG))

from automation_policy import (  # noqa:E402
    SHOPIFY_DAILY_JOB_NAME,
    SHOPIFY_DAILY_PARTITION_TAG,
    partition_deadline_utc,
    scheduled_tick_utc_for_partition,
)
from consumer_sla import SHOPIFY_DAILY_MART_ASSET_KEYS  # noqa:E402


SCHEDULE_NAME = "shopify_daily_partition_schedule"
SCHEDULE_AUTOMATION_TAG = "commerce/automation"
SCHEDULE_AUTOMATION_VALUE = "daily-schedule"
DEFAULT_LAUNCH_TOLERANCE_MINUTES = 10


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    raise TypeError(f"Unsupported timestamp value: {value!r}")


def _record_created_at(run_record: Any) -> datetime | None:
    for attr in ("create_timestamp", "start_time"):
        value = getattr(run_record, attr, None)
        if value is not None:
            return _as_utc_datetime(value)
    return None


def _event_timestamp(record: Any) -> datetime:
    entry = record.event_log_entry
    return datetime.fromtimestamp(entry.timestamp, tz=timezone.utc)


def collect_r01(instance: Any, partition_key: str, *, launch_tolerance_minutes: int) -> dict:
    import dagster as dg

    expected_tick = scheduled_tick_utc_for_partition(partition_key)
    deadline = partition_deadline_utc(partition_key)

    run_records = instance.get_run_records(
        filters=dg.RunsFilter(
            job_name=SHOPIFY_DAILY_JOB_NAME,
            statuses=[dg.DagsterRunStatus.SUCCESS],
            tags={
                SHOPIFY_DAILY_PARTITION_TAG: partition_key,
                SCHEDULE_AUTOMATION_TAG: SCHEDULE_AUTOMATION_VALUE,
            },
        ),
        limit=20,
    )

    if not run_records:
        return {
            "scenario": "R01",
            "partition_key": partition_key,
            "result": "FAIL",
            "reason": "no successful schedule-tagged run found for exact partition",
            "expected_schedule_tick_utc": _utc_iso(expected_tick),
            "deadline_utc": _utc_iso(deadline),
            "dagster_home": os.environ.get("DAGSTER_HOME"),
        }

    run_record = run_records[0]
    run = run_record.dagster_run
    created_at = _record_created_at(run_record)
    start_at = _as_utc_datetime(getattr(run_record, "start_time", None))
    end_at = _as_utc_datetime(getattr(run_record, "end_time", None))

    launch_delay_seconds = None
    launch_near_tick = False
    if created_at is not None:
        launch_delay_seconds = (created_at - expected_tick).total_seconds()
        launch_near_tick = 0 <= launch_delay_seconds <= launch_tolerance_minutes * 60

    marts: dict[str, dict] = {}
    missing_same_run: list[str] = []
    late_marts: list[str] = []

    for asset_key in SHOPIFY_DAILY_MART_ASSET_KEYS:
        result = instance.fetch_materializations(
            dg.AssetRecordsFilter(
                asset_key=dg.AssetKey([asset_key]),
                asset_partitions=[partition_key],
            ),
            limit=50,
        )
        same_run_records = [
            record
            for record in result.records
            if record.event_log_entry.run_id == run.run_id
        ]
        if not same_run_records:
            missing_same_run.append(asset_key)
            marts[asset_key] = {
                "materialized_by_schedule_run": False,
                "materialized_at_utc": None,
                "before_deadline": False,
            }
            continue

        materialized_at = max(_event_timestamp(record) for record in same_run_records)
        before_deadline = materialized_at <= deadline
        if not before_deadline:
            late_marts.append(asset_key)
        marts[asset_key] = {
            "materialized_by_schedule_run": True,
            "materialized_at_utc": _utc_iso(materialized_at),
            "before_deadline": before_deadline,
        }

    pass_conditions = {
        "successful_schedule_run_found": True,
        "schedule_run_near_expected_tick": launch_near_tick,
        "exact_partition_marts_same_run_8_of_8": not missing_same_run,
        "all_marts_before_01_00_utc_deadline": not late_marts and not missing_same_run,
    }
    passed = all(pass_conditions.values())

    return {
        "scenario": "R01",
        "partition_key": partition_key,
        "result": "PASS" if passed else "FAIL",
        "job_name": run.job_name,
        "run_id": run.run_id,
        "run_status": run.status.value,
        "run_tags": dict(sorted(run.tags.items())),
        "expected_schedule_tick_utc": _utc_iso(expected_tick),
        "run_created_at_utc": _utc_iso(created_at),
        "run_started_at_utc": _utc_iso(start_at),
        "run_completed_at_utc": _utc_iso(end_at),
        "launch_delay_seconds": launch_delay_seconds,
        "launch_tolerance_minutes": launch_tolerance_minutes,
        "deadline_utc": _utc_iso(deadline),
        "pass_conditions": pass_conditions,
        "missing_same_run_marts": missing_same_run,
        "late_marts": late_marts,
        "mart_materializations": marts,
        "dagster_home": os.environ.get("DAGSTER_HOME"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition-key", required=True, help="Exact YYYY-MM-DD partition")
    parser.add_argument(
        "--launch-tolerance-minutes",
        type=int,
        default=DEFAULT_LAUNCH_TOLERANCE_MINUTES,
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON evidence path. Parent directories are created automatically.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        import dagster as dg
    except ImportError as exc:
        print(f"Dagster runtime is not installed: {exc}", file=sys.stderr)
        return 2

    if not os.environ.get("DAGSTER_HOME"):
        print("DAGSTER_HOME is required so the verifier reads the real persistent instance.", file=sys.stderr)
        return 2

    instance = dg.DagsterInstance.get()
    report = collect_r01(
        instance,
        args.partition_key,
        launch_tolerance_minutes=args.launch_tolerance_minutes,
    )

    rendered = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
