#!/usr/bin/env python3
"""R09 Definition Runtime: Run SUCCESS does not imply exact-partition completeness.

The harness creates one real Dagster run for the daily job name and lets that run finish
SUCCESS while emitting exact-partition AssetMaterialization events for only 8 of the 9
consumer Mart assets.  The production Recovery State Reader and Recovery Sensor are then
evaluated after the freshness deadline.

Required behavior:

- Run Storage contains a SUCCESS run for the exact partition;
- Event Log contains 8/9 consumer Mart materializations for that same partition/run;
- the State Reader reports successful_run=true but materialized=false;
- the missing Mart is surfaced explicitly;
- Recovery Policy returns ALERT_MANUAL / successful_run_without_complete_partition;
- Sensor returns SkipReason and does not misclassify the partition as a missed schedule;
- no automatic recovery Run is persisted and replay budget remains 0.

This local persistent-instance harness proves the orchestration/state contract.  It does
not prove that a real dbt/Spark daily pipeline can naturally reach SUCCESS while omitting
a Mart, nor does it prove consumer-table row completeness inside Iceberg.  Those remain
real Runtime/data evidence boundaries.
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
FIXED_NOW = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
MISSING_MART = "fulfillment_events"
EXPECTED_PRESENT_MARTS = tuple(
    key for key in SHOPIFY_DAILY_MART_ASSET_KEYS if key != MISSING_MART
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _seed_success_incomplete_job() -> dg.JobDefinition:
    @dg.op
    def emit_partial_consumer_materializations():
        for asset_key in EXPECTED_PRESENT_MARTS:
            yield dg.AssetMaterialization(
                asset_key=dg.AssetKey([asset_key]),
                partition=PARTITION_KEY,
                metadata={
                    "acceptance_scenario": "R09",
                    "intentionally_incomplete": True,
                },
            )

    @dg.job(name=SHOPIFY_DAILY_JOB_NAME)
    def seed_daily_job():
        emit_partial_consumer_materializations()

    return seed_daily_job


def _partition_runs(instance: dg.DagsterInstance) -> tuple[dg.DagsterRun, ...]:
    records = instance.get_run_records(
        filters=dg.RunsFilter(
            job_name=SHOPIFY_DAILY_JOB_NAME,
            tags={SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY},
        ),
        limit=50,
    )
    return tuple(record.dagster_run for record in records)


def _materialization_run_ids(
    instance: dg.DagsterInstance,
    asset_key: str,
) -> tuple[str, ...]:
    result = instance.fetch_materializations(
        dg.AssetRecordsFilter(
            asset_key=dg.AssetKey([asset_key]),
            asset_partitions=[PARTITION_KEY],
        ),
        limit=20,
    )
    return tuple(record.event_log_entry.run_id for record in result.records)


def main() -> int:
    args = parse_args()

    with tempfile.TemporaryDirectory(prefix="commerce-r09-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            result = _seed_success_incomplete_job().execute_in_process(
                instance=instance,
                tags={
                    SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
                    "commerce/automation": "daily-schedule",
                    "commerce/acceptance": "r09-success-incomplete",
                },
            )
            run = instance.get_run_by_id(result.run_id)
            assert run is not None

            before_runs = _partition_runs(instance)
            before_auto_runs = tuple(
                candidate
                for candidate in before_runs
                if candidate.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
            )

            state = collect_partition_recovery_state(
                instance,
                partition_key=PARTITION_KEY,
                freshness_overdue=True,
                infrastructure_healthy=True,
                missed_schedule_eligible=True,
            )
            decision = decide_recovery(state.observation)

            present_same_run = {
                key: run.run_id in _materialization_run_ids(instance, key)
                for key in EXPECTED_PRESENT_MARTS
            }
            missing_run_ids = _materialization_run_ids(instance, MISSING_MART)

            with dg.build_sensor_context(instance=instance) as context:
                with (
                    patch.object(sensor_module, "utc_now", return_value=FIXED_NOW),
                    patch.object(
                        sensor_module,
                        "docker_compose_services_running",
                        return_value=True,
                    ),
                ):
                    sensor_result = shopify_daily_recovery_sensor(context)

            after_runs = _partition_runs(instance)
            after_auto_runs = tuple(
                candidate
                for candidate in after_runs
                if candidate.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
            )
            after_state = collect_partition_recovery_state(
                instance,
                partition_key=PARTITION_KEY,
                freshness_overdue=True,
                infrastructure_healthy=True,
                missed_schedule_eligible=True,
            )

            is_skip = isinstance(sensor_result, dg.SkipReason)
            conditions = {
                "daily_run_is_success": run.status is dg.DagsterRunStatus.SUCCESS,
                "eight_expected_marts_materialized_by_same_run": (
                    len(EXPECTED_PRESENT_MARTS) == 8
                    and all(present_same_run.values())
                ),
                "missing_mart_has_no_materialization": not missing_run_ids,
                "state_reader_sees_successful_run": state.observation.successful_run,
                "state_reader_does_not_call_partition_complete": (
                    not state.observation.materialized
                ),
                "state_reader_surfaces_exact_missing_mart": (
                    state.missing_mart_asset_keys == (MISSING_MART,)
                ),
                "policy_escalates_success_incomplete": (
                    decision.action is RecoveryAction.ALERT_MANUAL
                    and decision.reason_code
                    == "successful_run_without_complete_partition"
                ),
                "success_incomplete_prevents_missed_schedule_replay": (
                    decision.reason_code != "missed_schedule_or_no_run"
                ),
                "sensor_emits_skip_reason": is_skip,
                "no_partition_run_persisted_by_sensor": (
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
                "scenario": "R09-A",
                "partition_key": PARTITION_KEY,
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "successful_run_id": run.run_id,
                "successful_run_status": run.status.value,
                "expected_consumer_marts": list(SHOPIFY_DAILY_MART_ASSET_KEYS),
                "present_same_run_marts": [
                    key for key, present in present_same_run.items() if present
                ],
                "missing_marts": list(state.missing_mart_asset_keys),
                "observation": {
                    "successful_run": state.observation.successful_run,
                    "materialized": state.observation.materialized,
                    "failed_run": state.observation.failed_run,
                    "active_run": state.observation.active_run,
                    "auto_replay_attempts": state.observation.auto_replay_attempts,
                },
                "decision": {
                    "action": decision.action.value,
                    "reason_code": decision.reason_code,
                },
                "sensor_result_type": type(sensor_result).__name__,
                "sensor_skip_message": (
                    sensor_result.skip_message if is_skip else None
                ),
                "conditions": conditions,
                "does_not_prove": [
                    "a real dbt/Spark daily run naturally omitted a Mart while ending SUCCESS",
                    "Iceberg consumer-table row completeness beyond Dagster materialization events",
                    "real Daemon alert delivery or operator investigation workflow",
                ],
            }
            payload["result"] = "PASS" if all(conditions.values()) else "FAIL"

            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(payload, indent=2, sort_keys=True),
                    encoding="utf-8",
                )

            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
