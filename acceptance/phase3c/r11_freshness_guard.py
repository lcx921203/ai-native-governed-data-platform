#!/usr/bin/env python3
"""R11 Definition Runtime: freshness deadline blocks premature recovery.

The fixed story is the ``2026-08-05`` daily partition at ``2026-08-06 00:40 UTC``:

- the normal 00:15 UTC schedule window has already opened;
- the consumer freshness deadline is 01:00 UTC;
- the exact partition is still incomplete;
- a Daily Run already owns the partition;
- recovery must not intervene before the deadline.

The harness proves two distinct gates:

1. Policy gate: ``freshness_overdue=False`` returns
   ``WAIT / within_freshness_budget`` before Active Owner, infrastructure, replay budget,
   or failure-class recovery rules are considered.
2. Sensor candidate gate: ``overdue_partition_keys(00:40)`` does not contain
   ``2026-08-05``, so the production Recovery Sensor cannot issue a RunRequest for that
   partition before 01:00 UTC.

Older overdue partitions are materialized inside the temporary Dagster Event Store so
that they cannot distract the Sensor by becoming unrelated recovery candidates.

This is local Dagster orchestration/event-store evidence only. It does not prove a real
Dagster daemon tick, Dagster's preview Freshness evaluation service, a real dbt/Spark
Daily Run, or eventual 9/9 Iceberg consumer completeness.
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
from commerce_dagster.automation_policy import (
    SHOPIFY_DAILY_JOB_NAME,
    SHOPIFY_DAILY_PARTITION_TAG,
    latest_overdue_partition_key,
    overdue_partition_keys,
    partition_deadline_utc,
    scheduled_tick_utc_for_partition,
)
from commerce_dagster.consumer_sla import SHOPIFY_DAILY_MART_ASSET_KEYS
from commerce_dagster.recovery_policy import RecoveryAction, decide_recovery
from commerce_dagster.recovery_state_current import (
    AUTO_RECOVERY_TAG_VALUE,
    RECOVERY_TAG,
    collect_partition_recovery_state,
)
from commerce_dagster.sensors import shopify_daily_recovery_sensor


PARTITION_KEY = "2026-08-05"
BEFORE_DEADLINE = datetime(2026, 8, 6, 0, 40, tzinfo=timezone.utc)
AT_DEADLINE = datetime(2026, 8, 6, 1, 0, tzinfo=timezone.utc)
OLDER_COMPLETE_JOB_NAME = "r11_seed_older_complete_partitions"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _daily_seed_job() -> dg.JobDefinition:
    @dg.op
    def seed_noop():
        return None

    @dg.job(name=SHOPIFY_DAILY_JOB_NAME)
    def seed_daily_job():
        seed_noop()

    return seed_daily_job


def _seed_active_daily_run(
    instance: dg.DagsterInstance,
    job_def: dg.JobDefinition,
) -> dg.DagsterRun:
    """Persist a not-yet-started normal Daily Run that owns the exact partition."""

    run = instance.create_run_for_job(
        job_def=job_def,
        tags={
            SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
            "commerce/automation": "daily-schedule",
            "commerce/acceptance": "r11-before-freshness-deadline",
        },
    )
    refreshed = instance.get_run_by_id(run.run_id)
    assert refreshed is not None
    return refreshed


def _older_complete_job(partition_keys: tuple[str, ...]) -> dg.JobDefinition:
    @dg.op
    def emit_older_complete_materializations():
        for partition_key in partition_keys:
            for asset_key in SHOPIFY_DAILY_MART_ASSET_KEYS:
                yield dg.AssetMaterialization(
                    asset_key=dg.AssetKey([asset_key]),
                    partition=partition_key,
                    metadata={
                        "acceptance_scenario": "R11",
                        "purpose": "keep-older-overdue-partitions-non-actionable",
                    },
                )

    @dg.job(name=OLDER_COMPLETE_JOB_NAME)
    def older_complete_job():
        emit_older_complete_materializations()

    return older_complete_job


def _partition_runs(instance: dg.DagsterInstance) -> tuple[dg.DagsterRun, ...]:
    records = instance.get_run_records(
        filters=dg.RunsFilter(
            job_name=SHOPIFY_DAILY_JOB_NAME,
            tags={SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY},
        ),
        limit=50,
    )
    return tuple(record.dagster_run for record in records)


def _auto_recovery_runs(instance: dg.DagsterInstance) -> tuple[dg.DagsterRun, ...]:
    return tuple(
        run
        for run in _partition_runs(instance)
        if run.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
    )


def main() -> int:
    args = parse_args()

    expected_tick = scheduled_tick_utc_for_partition(PARTITION_KEY)
    expected_deadline = partition_deadline_utc(PARTITION_KEY)
    candidates_before = overdue_partition_keys(BEFORE_DEADLINE)
    candidates_at_deadline = overdue_partition_keys(AT_DEADLINE)

    with tempfile.TemporaryDirectory(prefix="commerce-r11-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            active_daily_run = _seed_active_daily_run(instance, _daily_seed_job())

            # Keep all genuinely overdue historical candidates complete. This isolates the
            # R11 assertion to the not-yet-overdue 2026-08-05 partition.
            if candidates_before:
                older_result = _older_complete_job(candidates_before).execute_in_process(
                    instance=instance,
                    tags={
                        "commerce/automation": "acceptance-seed",
                        "commerce/acceptance": "r11-older-partitions-complete",
                    },
                )
                older_seed_success = older_result.success
            else:
                older_seed_success = True

            before_runs = _partition_runs(instance)
            before_auto_runs = _auto_recovery_runs(instance)

            state_before_deadline = collect_partition_recovery_state(
                instance,
                partition_key=PARTITION_KEY,
                freshness_overdue=False,
                infrastructure_healthy=False,
                missed_schedule_eligible=False,
            )
            decision_before_deadline = decide_recovery(
                state_before_deadline.observation
            )

            with dg.build_sensor_context(instance=instance) as context:
                with (
                    patch.object(sensor_module, "utc_now", return_value=BEFORE_DEADLINE),
                    patch.object(
                        sensor_module,
                        "docker_compose_services_running",
                        return_value=True,
                    ),
                ):
                    sensor_result = shopify_daily_recovery_sensor(context)

            after_runs = _partition_runs(instance)
            after_auto_runs = _auto_recovery_runs(instance)
            after_state = collect_partition_recovery_state(
                instance,
                partition_key=PARTITION_KEY,
                freshness_overdue=False,
                infrastructure_healthy=True,
                missed_schedule_eligible=False,
            )

            is_skip = isinstance(sensor_result, dg.SkipReason)
            conditions = {
                "schedule_tick_is_00_15_utc": (
                    expected_tick
                    == datetime(2026, 8, 6, 0, 15, tzinfo=timezone.utc)
                ),
                "freshness_deadline_is_01_00_utc": expected_deadline == AT_DEADLINE,
                "target_partition_not_overdue_at_00_40": (
                    PARTITION_KEY not in candidates_before
                ),
                "target_partition_enters_candidates_at_01_00": (
                    PARTITION_KEY in candidates_at_deadline
                    and latest_overdue_partition_key(AT_DEADLINE) == PARTITION_KEY
                ),
                "active_daily_run_owns_target_partition": (
                    active_daily_run.status in {
                        dg.DagsterRunStatus.NOT_STARTED,
                        dg.DagsterRunStatus.QUEUED,
                        dg.DagsterRunStatus.STARTING,
                        dg.DagsterRunStatus.STARTED,
                    }
                    and state_before_deadline.observation.active_run
                    and active_daily_run.run_id in state_before_deadline.active_run_ids
                ),
                "target_partition_is_still_incomplete": (
                    not state_before_deadline.observation.materialized
                    and len(state_before_deadline.missing_mart_asset_keys)
                    == len(SHOPIFY_DAILY_MART_ASSET_KEYS)
                ),
                "freshness_guard_waits_before_other_recovery_rules": (
                    decision_before_deadline.action is RecoveryAction.WAIT
                    and decision_before_deadline.reason_code
                    == "within_freshness_budget"
                ),
                "older_overdue_candidates_are_non_actionable": older_seed_success,
                "sensor_emits_no_recovery_request_before_deadline": is_skip,
                "target_partition_run_count_unchanged": (
                    len(after_runs) == len(before_runs)
                ),
                "no_auto_recovery_run_persisted": (
                    len(before_auto_runs) == 0 and len(after_auto_runs) == 0
                ),
                "replay_budget_remains_zero": (
                    after_state.observation.auto_replay_attempts == 0
                ),
            }

            payload = {
                "scenario": "R11-A",
                "partition_key": PARTITION_KEY,
                "scheduled_tick_utc": expected_tick.isoformat(),
                "before_deadline_utc": BEFORE_DEADLINE.isoformat(),
                "deadline_utc": expected_deadline.isoformat(),
                "candidates_before_deadline": list(candidates_before),
                "candidates_at_deadline": list(candidates_at_deadline),
                "active_daily_run_id": active_daily_run.run_id,
                "active_daily_run_status": active_daily_run.status.value,
                "observation_before_deadline": {
                    "freshness_overdue": (
                        state_before_deadline.observation.freshness_overdue
                    ),
                    "materialized": state_before_deadline.observation.materialized,
                    "active_run": state_before_deadline.observation.active_run,
                    "missing_mart_asset_keys": list(
                        state_before_deadline.missing_mart_asset_keys
                    ),
                    "auto_replay_attempts": (
                        state_before_deadline.observation.auto_replay_attempts
                    ),
                },
                "decision_before_deadline": {
                    "action": decision_before_deadline.action.value,
                    "reason_code": decision_before_deadline.reason_code,
                },
                "sensor_result_type": type(sensor_result).__name__,
                "sensor_skip_message": (
                    sensor_result.skip_message if is_skip else None
                ),
                "conditions": conditions,
                "does_not_prove": [
                    "a real Dagster daemon evaluated the recovery sensor at 00:40 UTC",
                    "Dagster preview Freshness evaluation produced runtime evidence",
                    "a real dbt/Spark Daily Run remained incomplete until 00:40 UTC",
                    "the exact Iceberg consumer partition eventually completed 9/9",
                ],
                "result": "PASS" if all(conditions.values()) else "FAIL",
            }

    rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")

    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
