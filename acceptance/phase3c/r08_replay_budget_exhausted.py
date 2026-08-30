#!/usr/bin/env python3
"""R08 Definition Runtime: failed automatic replay exhausts the bounded budget.

This harness persists:

1. a historical failed daily-schedule run for one exact partition;
2. automatic Recovery attempt-1 for the same partition;
3. attempt-1 transitioned to FAILURE.

After attempt-1 is no longer active, the production state reader must still count the
persisted recovery run as auto_replay_attempts=1.  The production Recovery Policy and
Sensor must then fail closed:

- ALERT_MANUAL / auto_replay_budget_exhausted;
- Sensor returns SkipReason, not RunRequest;
- no attempt-2 run is persisted;
- the replay budget remains 1 and survives a fresh state read.

This is local persistent-Dagster evidence.  It does not prove a real daemon launched
attempt-1, executed it, or delivered an external incident alert.
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
FIXED_NOW = datetime(2026, 8, 6, 1, 20, tzinfo=timezone.utc)
FIRST_RECOVERY_RUN_KEY = "shopify-daily-recovery:2026-08-05:attempt-1"
FORBIDDEN_SECOND_RUN_KEY = "shopify-daily-recovery:2026-08-05:attempt-2"


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
            reason_code="r08_seeded_initial_transient_failure",
        ),
    }
    run = instance.create_run_for_job(job_def=job_def, tags=tags)
    instance.report_run_failed(run)
    refreshed = instance.get_run_by_id(run.run_id)
    assert refreshed is not None
    return refreshed


def _seed_failed_recovery_attempt_one(
    instance: dg.DagsterInstance,
    job_def: dg.JobDefinition,
) -> dg.DagsterRun:
    """Persist attempt-1 and mark it failed.

    The failure remains replay-safe in isolation (TRANSIENT_RUNTIME) so R08 proves that
    the bounded cross-run budget, not the failure class, blocks another automatic replay.
    """

    tags = {
        SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
        "commerce/automation": "recovery-sensor",
        RECOVERY_TAG: AUTO_RECOVERY_TAG_VALUE,
        RECOVERY_ATTEMPT_TAG: "1",
        RECOVERY_REASON_TAG: "transient_failure_after_runtime_recovered",
        "commerce/acceptance_expected_run_key": FIRST_RECOVERY_RUN_KEY,
        **failure_class_tags(
            FailureClass.TRANSIENT_RUNTIME,
            source=FailureClassSource.EXECUTION_ADAPTER,
            component="spark-thrift",
            reason_code="r08_seeded_recovery_attempt_failed",
        ),
    }
    run = instance.create_run_for_job(job_def=job_def, tags=tags)
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


def main() -> int:
    args = parse_args()

    with tempfile.TemporaryDirectory(prefix="commerce-r08-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            job_def = _seed_job()
            failed_daily = _seed_failed_daily_run(instance, job_def)
            failed_recovery = _seed_failed_recovery_attempt_one(instance, job_def)

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
            attempt_tags = tuple(
                run.tags.get(RECOVERY_ATTEMPT_TAG)
                for run in after_auto_runs
            )
            created_run_keys = tuple(
                run.tags.get("commerce/acceptance_expected_run_key")
                for run in after_auto_runs
            )

            conditions = {
                "daily_run_failed": failed_daily.status is dg.DagsterRunStatus.FAILURE,
                "attempt_one_failed": (
                    failed_recovery.status is dg.DagsterRunStatus.FAILURE
                ),
                "attempt_one_is_not_active": not before_state.observation.active_run,
                "attempt_one_is_persisted_budget": (
                    before_state.observation.auto_replay_attempts == 1
                ),
                "latest_failure_remains_replay_safe": (
                    before_state.observation.failure_class
                    is FailureClass.TRANSIENT_RUNTIME
                ),
                "policy_escalates_manual": (
                    decision.action is RecoveryAction.ALERT_MANUAL
                ),
                "policy_reason_budget_exhausted": (
                    decision.reason_code == "auto_replay_budget_exhausted"
                ),
                "sensor_emits_skip_reason": is_skip,
                "sensor_summary_contains_budget_reason": (
                    is_skip
                    and skip_message is not None
                    and "auto_replay_budget_exhausted" in skip_message
                ),
                "partition_run_count_unchanged": len(after_runs) == len(before_runs),
                "auto_recovery_run_count_stays_one": (
                    len(before_auto_runs) == 1 and len(after_auto_runs) == 1
                ),
                "replay_budget_stays_one_after_sensor": (
                    after_state.observation.auto_replay_attempts == 1
                ),
                "attempt_two_not_persisted": "2" not in attempt_tags,
                "attempt_two_run_key_not_persisted": (
                    FORBIDDEN_SECOND_RUN_KEY not in created_run_keys
                ),
            }

            payload = {
                "scenario": "R08-A",
                "partition_key": PARTITION_KEY,
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "failed_daily_run_id": failed_daily.run_id,
                "failed_recovery_run_id": failed_recovery.run_id,
                "failed_recovery_expected_run_key": FIRST_RECOVERY_RUN_KEY,
                "decision": {
                    "action": decision.action.value,
                    "reason_code": decision.reason_code,
                    "explanation": decision.explanation,
                },
                "sensor_result_type": type(sensor_result).__name__,
                "sensor_skip_message": skip_message,
                "partition_run_count_before": len(before_runs),
                "partition_run_count_after": len(after_runs),
                "auto_recovery_run_count_before": len(before_auto_runs),
                "auto_recovery_run_count_after": len(after_auto_runs),
                "auto_replay_attempts_before": (
                    before_state.observation.auto_replay_attempts
                ),
                "auto_replay_attempts_after": (
                    after_state.observation.auto_replay_attempts
                ),
                "conditions": conditions,
                "result": "PASS" if all(conditions.values()) else "FAIL",
                "evidence_level": "C1-local-dagster-runtime",
                "does_not_prove": [
                    "a real Dagster daemon launched attempt-1",
                    "attempt-1 actually executed against Docker/Spark and failed",
                    "an external incident alert was delivered",
                    "operator remediation or a later manual replay succeeded",
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
