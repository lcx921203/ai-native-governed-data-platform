#!/usr/bin/env python3
"""R10 Definition Runtime: current exact-partition completeness wins over history.

The harness persists one historical failed daily run for ``2026-08-05`` and then uses a
separate manual-repair/backfill job to emit exact-partition ``AssetMaterialization``
events for all 9 consumer Mart assets.

Required behavior after the consumer deadline:

- the historical Daily Run is still FAILURE and remains visible to the State Reader;
- all 9 exact-partition Mart materializations are present in Dagster Event Storage;
- the production State Reader reports ``materialized=true`` even though
  ``failed_run=true``;
- Recovery Policy returns ``NO_ACTION / partition_already_materialized``;
- the production Recovery Sensor emits ``SkipReason`` and persists no recovery Run;
- historical failure status must not override current exact-partition completeness.

This is local persistent-Dagster orchestration/event-store evidence.  It does not prove
Iceberg row-level completeness, nor does it prove a real manual backfill or repair run
executed dbt/Spark successfully.
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
from commerce_dagster.failure_classification import (
    FailureClass,
    FailureClassSource,
    failure_class_tags,
)
from commerce_dagster.recovery_policy import RecoveryAction, decide_recovery
from commerce_dagster.recovery_state_current import (
    AUTO_RECOVERY_TAG_VALUE,
    RECOVERY_TAG,
    collect_partition_recovery_state,
)
from commerce_dagster.sensors import shopify_daily_recovery_sensor


PARTITION_KEY = "2026-08-05"
FIXED_NOW = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
REPAIR_JOB_NAME = "r10_manual_repair_job"


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


def _repair_job() -> dg.JobDefinition:
    @dg.op
    def emit_complete_consumer_materializations():
        for asset_key in SHOPIFY_DAILY_MART_ASSET_KEYS:
            yield dg.AssetMaterialization(
                asset_key=dg.AssetKey([asset_key]),
                partition=PARTITION_KEY,
                metadata={
                    "acceptance_scenario": "R10",
                    "repair_mode": "manual-backfill-probe",
                },
            )

    @dg.job(name=REPAIR_JOB_NAME)
    def repair_job():
        emit_complete_consumer_materializations()

    return repair_job


def _seed_failed_daily_run(
    instance: dg.DagsterInstance,
    job_def: dg.JobDefinition,
) -> dg.DagsterRun:
    run = instance.create_run_for_job(
        job_def=job_def,
        tags={
            SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
            "commerce/automation": "daily-schedule",
            **failure_class_tags(
                FailureClass.TRANSIENT_RUNTIME,
                source=FailureClassSource.EXECUTION_ADAPTER,
                component="spark-thrift",
                reason_code="r10_seeded_historical_transient_failure",
            ),
        },
    )
    instance.report_run_failed(run)
    refreshed = instance.get_run_by_id(run.run_id)
    assert refreshed is not None
    return refreshed


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

    with tempfile.TemporaryDirectory(prefix="commerce-r10-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            failed_daily_run = _seed_failed_daily_run(instance, _daily_seed_job())

            repair_result = _repair_job().execute_in_process(
                instance=instance,
                tags={
                    SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
                    "commerce/automation": "manual-backfill",
                    "commerce/acceptance": "r10-partition-complete",
                },
            )
            repair_run = instance.get_run_by_id(repair_result.run_id)
            assert repair_run is not None

            before_runs = _partition_runs(instance)
            before_auto_runs = _auto_recovery_runs(instance)

            state = collect_partition_recovery_state(
                instance,
                partition_key=PARTITION_KEY,
                freshness_overdue=True,
                infrastructure_healthy=True,
                missed_schedule_eligible=True,
            )
            decision = decide_recovery(state.observation)

            repair_materializations = {
                asset_key: repair_run.run_id
                in _materialization_run_ids(instance, asset_key)
                for asset_key in SHOPIFY_DAILY_MART_ASSET_KEYS
            }

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
            after_auto_runs = _auto_recovery_runs(instance)
            after_state = collect_partition_recovery_state(
                instance,
                partition_key=PARTITION_KEY,
                freshness_overdue=True,
                infrastructure_healthy=True,
                missed_schedule_eligible=True,
            )

            is_skip = isinstance(sensor_result, dg.SkipReason)
            skip_message = sensor_result.skip_message if is_skip else None
            conditions = {
                "historical_daily_run_is_failure": (
                    failed_daily_run.status is dg.DagsterRunStatus.FAILURE
                ),
                "repair_run_is_success": repair_run.status is dg.DagsterRunStatus.SUCCESS,
                "all_nine_marts_materialized_by_repair_run": (
                    len(SHOPIFY_DAILY_MART_ASSET_KEYS) == 9
                    and all(repair_materializations.values())
                ),
                "state_reader_still_sees_historical_failure": (
                    state.observation.failed_run
                    and failed_daily_run.run_id in state.failed_run_ids
                ),
                "state_reader_calls_exact_partition_complete": (
                    state.observation.materialized
                    and state.missing_mart_asset_keys == ()
                ),
                "historical_failure_class_remains_visible": (
                    state.observation.failure_class is FailureClass.TRANSIENT_RUNTIME
                ),
                "current_completeness_wins_over_failure_history": (
                    decision.action is RecoveryAction.NO_ACTION
                    and decision.reason_code == "partition_already_materialized"
                ),
                "sensor_emits_skip_reason": is_skip,
                "sensor_summary_contains_complete_reason": (
                    is_skip
                    and skip_message is not None
                    and f"{PARTITION_KEY}:partition_already_materialized"
                    in skip_message
                ),
                "daily_partition_run_count_unchanged": (
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
                "scenario": "R10-A",
                "partition_key": PARTITION_KEY,
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "historical_failed_daily_run_id": failed_daily_run.run_id,
                "historical_failed_daily_run_status": failed_daily_run.status.value,
                "repair_run_id": repair_run.run_id,
                "repair_run_status": repair_run.status.value,
                "repair_job_name": REPAIR_JOB_NAME,
                "repair_materializations": repair_materializations,
                "observation": {
                    "materialized": state.observation.materialized,
                    "failed_run": state.observation.failed_run,
                    "successful_daily_run": state.observation.successful_run,
                    "failure_class": state.observation.failure_class.value,
                    "missing_marts": list(state.missing_mart_asset_keys),
                    "auto_replay_attempts": state.observation.auto_replay_attempts,
                },
                "decision": {
                    "action": decision.action.value,
                    "reason_code": decision.reason_code,
                    "explanation": decision.explanation,
                },
                "sensor_result_type": type(sensor_result).__name__,
                "sensor_skip_message": skip_message,
                "conditions": conditions,
                "does_not_prove": [
                    "Iceberg row-level completeness for the 9 consumer tables",
                    "a real operator/manual backfill executed dbt and Spark successfully",
                    "real Daemon polling or external incident-resolution workflow",
                ],
            }
            payload["result"] = "PASS" if all(conditions.values()) else "FAIL"

            rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
            print(rendered)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + "\n", encoding="utf-8")
            return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
