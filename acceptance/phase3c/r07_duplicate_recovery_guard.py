#!/usr/bin/env python3
"""R07 Definition Runtime: an active recovery run owns the exact partition.

This harness seeds two persisted runs for the same daily partition:

1. a historical failed daily-schedule run;
2. a first automatic recovery run that has already been created and is still active
   (NOT_STARTED is intentionally sufficient: ownership begins as soon as the Run is
   persisted, before execution starts).

The Recovery Sensor is then evaluated again after the freshness deadline while current
infrastructure is healthy.  Required behavior:

- the exact-partition state reader sees the active recovery owner;
- the already-created recovery run counts as auto_replay_attempts=1;
- active ownership takes precedence over replay-budget exhaustion;
- policy returns WAIT / active_run_owns_partition;
- Sensor returns SkipReason, not another RunRequest;
- no additional partition run or automatic-recovery run is persisted.

This proves the application-level Run-Storage duplicate guard.  It does not prove the
Dagster daemon's run_key deduplication path; that remains real-daemon evidence.
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
from commerce_dagster.failure_classification import (
    FailureClass,
    FailureClassSource,
    failure_class_tags,
)
from commerce_dagster.recovery_policy import RecoveryAction, decide_recovery
from commerce_dagster.recovery_state_current import (
    AUTO_RECOVERY_TAG_VALUE,
    RECOVERY_ATTEMPT_TAG,
    RECOVERY_REASON_TAG,
    RECOVERY_TAG,
    collect_partition_recovery_state,
)
from commerce_dagster.sensors import shopify_daily_recovery_sensor


PARTITION_KEY = "2026-08-05"
FIXED_NOW = datetime(2026, 8, 6, 1, 10, tzinfo=timezone.utc)
FIRST_RECOVERY_RUN_KEY = "shopify-daily-recovery:2026-08-05:attempt-1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _seed_job() -> dg.JobDefinition:
    @dg.op
    def seed_noop():
        return None

    @dg.job(name=SHOPIFY_DAILY_JOB_NAME)
    def seed_daily_job():
        seed_noop()

    return seed_daily_job


def _seed_failed_daily_run(
    instance: dg.DagsterInstance,
    job_def: dg.JobDefinition,
) -> dg.DagsterRun:
    tags = {
        SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
        "commerce/automation": "daily-schedule",
        **failure_class_tags(
            FailureClass.TRANSIENT_RUNTIME,
            source=FailureClassSource.EXECUTION_ADAPTER,
            component="spark-thrift",
            reason_code="r07_seeded_transient_failure",
        ),
    }
    run = instance.create_run_for_job(job_def=job_def, tags=tags)
    instance.report_run_failed(run)
    refreshed = instance.get_run_by_id(run.run_id)
    assert refreshed is not None
    return refreshed


def _seed_active_recovery_run(
    instance: dg.DagsterInstance,
    job_def: dg.JobDefinition,
) -> dg.DagsterRun:
    """Persist attempt-1 and deliberately leave it NOT_STARTED.

    NOT_STARTED is an active ownership state in the production state reader.  This is
    the earliest point at which another Sensor tick must stop trying to create a second
    recovery owner for the same exact partition.
    """

    tags = {
        SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
        "commerce/automation": "recovery-sensor",
        RECOVERY_TAG: AUTO_RECOVERY_TAG_VALUE,
        RECOVERY_ATTEMPT_TAG: "1",
        RECOVERY_REASON_TAG: "transient_failure_after_runtime_recovered",
        "commerce/acceptance_expected_run_key": FIRST_RECOVERY_RUN_KEY,
    }
    run = instance.create_run_for_job(job_def=job_def, tags=tags)
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


def main() -> int:
    args = parse_args()

    with tempfile.TemporaryDirectory(prefix="commerce-r07-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            job_def = _seed_job()
            failed_run = _seed_failed_daily_run(instance, job_def)
            active_recovery_run = _seed_active_recovery_run(instance, job_def)

            before_runs = _partition_runs(instance)
            before_auto_runs = _auto_recovery_runs(instance)
            before_state = collect_partition_recovery_state(
                instance,
                partition_key=PARTITION_KEY,
                freshness_overdue=True,
                infrastructure_healthy=True,
                missed_schedule_eligible=True,
            )
            decision = decide_recovery(before_state.observation)

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
                "historical_failed_run_present": (
                    failed_run.status is dg.DagsterRunStatus.FAILURE
                ),
                "recovery_owner_is_active": (
                    active_recovery_run.status in {
                        dg.DagsterRunStatus.NOT_STARTED,
                        dg.DagsterRunStatus.QUEUED,
                        dg.DagsterRunStatus.STARTING,
                        dg.DagsterRunStatus.STARTED,
                        dg.DagsterRunStatus.MANAGED,
                        dg.DagsterRunStatus.CANCELING,
                    }
                    and active_recovery_run.run_id in before_state.active_run_ids
                ),
                "active_owner_visible_to_policy": before_state.observation.active_run,
                "first_recovery_already_consumes_one_persisted_attempt": (
                    before_state.observation.auto_replay_attempts == 1
                ),
                "active_owner_precedes_budget_exhaustion": (
                    decision.action is RecoveryAction.WAIT
                    and decision.reason_code == "active_run_owns_partition"
                ),
                "sensor_emits_skip_not_run_request": is_skip,
                "sensor_summary_contains_active_owner_reason": (
                    is_skip
                    and skip_message is not None
                    and f"{PARTITION_KEY}:active_run_owns_partition" in skip_message
                ),
                "partition_run_count_unchanged": len(after_runs) == len(before_runs),
                "auto_recovery_run_count_unchanged": (
                    len(after_auto_runs) == len(before_auto_runs) == 1
                ),
                "same_active_owner_remains": (
                    active_recovery_run.run_id in after_state.active_run_ids
                ),
                "replay_budget_not_incremented_by_sensor_poll": (
                    after_state.observation.auto_replay_attempts == 1
                ),
            }

            payload = {
                "scenario": "R07-A",
                "result": "PASS" if all(conditions.values()) else "FAIL",
                "partition_key": PARTITION_KEY,
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "failed_run_id": failed_run.run_id,
                "active_recovery_run_id": active_recovery_run.run_id,
                "active_recovery_status": active_recovery_run.status.value,
                "expected_first_recovery_run_key": FIRST_RECOVERY_RUN_KEY,
                "policy_action": decision.action.value,
                "reason_code": decision.reason_code,
                "sensor_result_type": type(sensor_result).__name__,
                "skip_message": skip_message,
                "partition_run_count_before": len(before_runs),
                "partition_run_count_after": len(after_runs),
                "auto_recovery_run_count_before": len(before_auto_runs),
                "auto_recovery_run_count_after": len(after_auto_runs),
                "auto_replay_attempts_before": before_state.observation.auto_replay_attempts,
                "auto_replay_attempts_after": after_state.observation.auto_replay_attempts,
                "conditions": conditions,
                "evidence_level": "C1-local-dagster-runtime",
                "does_not_prove": [
                    "a real Dagster daemon persisted attempt-1 from a Sensor RunRequest",
                    "Dagster daemon run_key deduplication rejected a duplicate request",
                    "the active recovery run actually executed or completed",
                    "the recovery run materialized 9/9 exact-partition marts",
                ],
            }

    rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
