#!/usr/bin/env python3
"""R04 Definition Runtime: repeated infrastructure-down sensor ticks stay bounded.

This acceptance harness proves the waiting invariant around an already-failed exact
partition.  A historical daily run has already failed with
``infrastructure_unavailable``.  The Recovery Sensor is then evaluated at three
post-deadline ticks while current infrastructure health remains false.

The required behavior is deliberately strict:

- every down tick returns SkipReason / ALERT_AND_WAIT;
- no Recovery Run is created merely because the Sensor keeps polling;
- ``auto_replay_attempts`` remains zero while waiting;
- once infrastructure becomes healthy, the first eligible recovery request is still
  ``attempt-1`` for the exact same partition.

This proves local persistent-state / SensorDefinition behavior.  It does not prove a
real Dagster daemon polling loop or a real Docker outage.
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
DOWN_TICKS = (
    datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc),
    datetime(2026, 8, 6, 1, 10, tzinfo=timezone.utc),
    datetime(2026, 8, 6, 1, 15, tzinfo=timezone.utc),
)
RECOVERED_TICK = datetime(2026, 8, 6, 1, 20, tzinfo=timezone.utc)
EXPECTED_RECOVERY_RUN_KEY = "shopify-daily-recovery:2026-08-05:attempt-1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _seed_failed_daily_run(instance: dg.DagsterInstance) -> dg.DagsterRun:
    @dg.op
    def seed_noop():
        return None

    @dg.job(name=SHOPIFY_DAILY_JOB_NAME)
    def seed_daily_job():
        seed_noop()

    tags = {
        SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY,
        "commerce/automation": "daily-schedule",
        **failure_class_tags(
            FailureClass.INFRASTRUCTURE_UNAVAILABLE,
            source=FailureClassSource.EXECUTION_ADAPTER,
            component="spark-thrift",
            reason_code="r04_seeded_infrastructure_outage",
        ),
    }
    run = instance.create_run_for_job(job_def=seed_daily_job, tags=tags)
    instance.report_run_failed(run)
    refreshed = instance.get_run_by_id(run.run_id)
    if refreshed is None:
        raise RuntimeError("seeded failed daily run could not be reloaded")
    return refreshed


def _partition_run_count(instance: dg.DagsterInstance) -> int:
    return len(
        instance.get_run_records(
            filters=dg.RunsFilter(
                job_name=SHOPIFY_DAILY_JOB_NAME,
                tags={SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY},
            ),
            limit=50,
        )
    )


def _evaluate_down_tick(
    instance: dg.DagsterInstance,
    tick: datetime,
) -> dict:
    before_state = collect_partition_recovery_state(
        instance,
        partition_key=PARTITION_KEY,
        freshness_overdue=True,
        infrastructure_healthy=False,
        missed_schedule_eligible=True,
    )
    before_runs = _partition_run_count(instance)
    decision = decide_recovery(before_state.observation)

    with dg.build_sensor_context(instance=instance) as context:
        with (
            patch.object(sensor_module, "utc_now", return_value=tick),
            patch.object(
                sensor_module,
                "docker_compose_services_running",
                return_value=False,
            ),
        ):
            sensor_result = shopify_daily_recovery_sensor(context)

    after_state = collect_partition_recovery_state(
        instance,
        partition_key=PARTITION_KEY,
        freshness_overdue=True,
        infrastructure_healthy=False,
        missed_schedule_eligible=True,
    )
    after_runs = _partition_run_count(instance)
    skip_message = (
        sensor_result.skip_message
        if isinstance(sensor_result, dg.SkipReason)
        else None
    )

    conditions = {
        "policy_alerts_and_waits": (
            decision.action is RecoveryAction.ALERT_AND_WAIT
            and decision.reason_code == "infrastructure_unhealthy"
        ),
        "sensor_emits_skip_reason": isinstance(sensor_result, dg.SkipReason),
        "skip_mentions_target_infrastructure_wait": bool(
            skip_message
            and f"{PARTITION_KEY}:infrastructure_unhealthy" in skip_message
        ),
        "budget_zero_before_tick": before_state.observation.auto_replay_attempts == 0,
        "budget_zero_after_tick": after_state.observation.auto_replay_attempts == 0,
        "partition_run_count_unchanged": before_runs == after_runs,
        "no_active_recovery_owner_created": not after_state.observation.active_run,
    }
    return {
        "tick_utc": tick.isoformat(),
        "policy_action": decision.action.value,
        "reason_code": decision.reason_code,
        "sensor_result_type": type(sensor_result).__name__,
        "skip_message": skip_message,
        "auto_replay_attempts_before": before_state.observation.auto_replay_attempts,
        "auto_replay_attempts_after": after_state.observation.auto_replay_attempts,
        "partition_run_count_before": before_runs,
        "partition_run_count_after": after_runs,
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def _evaluate_first_tick_after_restore(instance: dg.DagsterInstance) -> dict:
    state = collect_partition_recovery_state(
        instance,
        partition_key=PARTITION_KEY,
        freshness_overdue=True,
        infrastructure_healthy=True,
        missed_schedule_eligible=True,
    )
    decision = decide_recovery(state.observation)

    with dg.build_sensor_context(instance=instance) as context:
        with (
            patch.object(sensor_module, "utc_now", return_value=RECOVERED_TICK),
            patch.object(
                sensor_module,
                "docker_compose_services_running",
                return_value=True,
            ),
        ):
            sensor_result = shopify_daily_recovery_sensor(context)

    is_request = isinstance(sensor_result, dg.RunRequest)
    conditions = {
        "wait_ticks_did_not_consume_budget": state.observation.auto_replay_attempts == 0,
        "restored_policy_allows_auto_replay": (
            decision.action is RecoveryAction.AUTO_REPLAY
            and decision.reason_code
            == "infrastructure_failure_after_runtime_recovered"
        ),
        "sensor_emits_first_recovery_request": is_request,
        "exact_partition_preserved": (
            is_request and sensor_result.partition_key == PARTITION_KEY
        ),
        "first_recovery_is_attempt_one": (
            is_request and sensor_result.run_key == EXPECTED_RECOVERY_RUN_KEY
        ),
        "recovery_auto_tag_present": (
            is_request
            and sensor_result.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
        ),
        "recovery_attempt_tag_is_one": (
            is_request and sensor_result.tags.get(RECOVERY_ATTEMPT_TAG) == "1"
        ),
        "recovery_reason_matches_restore": (
            is_request
            and sensor_result.tags.get(RECOVERY_REASON_TAG)
            == "infrastructure_failure_after_runtime_recovered"
        ),
    }
    return {
        "tick_utc": RECOVERED_TICK.isoformat(),
        "policy_action": decision.action.value,
        "reason_code": decision.reason_code,
        "sensor_result_type": type(sensor_result).__name__,
        "run_key": sensor_result.run_key if is_request else None,
        "partition_key": sensor_result.partition_key if is_request else None,
        "tags": dict(sensor_result.tags) if is_request else {},
        "auto_replay_attempts_before_request": state.observation.auto_replay_attempts,
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def main() -> int:
    args = parse_args()

    with tempfile.TemporaryDirectory(prefix="commerce-r04-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            seeded_run = _seed_failed_daily_run(instance)
            initial_run_count = _partition_run_count(instance)

            down_results = tuple(
                _evaluate_down_tick(instance, tick)
                for tick in DOWN_TICKS
            )
            run_count_after_waits = _partition_run_count(instance)
            recovered = _evaluate_first_tick_after_restore(instance)

            conditions = {
                "seeded_run_failed": seeded_run.status is dg.DagsterRunStatus.FAILURE,
                "three_down_ticks_all_pass": all(
                    item["result"] == "PASS" for item in down_results
                ),
                "waiting_created_no_new_runs": (
                    run_count_after_waits == initial_run_count
                ),
                "first_tick_after_restore_passes": recovered["result"] == "PASS",
            }
            payload = {
                "scenario": "R04-A",
                "result": "PASS" if all(conditions.values()) else "FAIL",
                "partition_key": PARTITION_KEY,
                "seeded_failed_run_id": seeded_run.run_id,
                "down_ticks": down_results,
                "recovered_tick": recovered,
                "conditions": conditions,
                "evidence_level": "C1-local-dagster-runtime",
                "does_not_prove": [
                    "a real Dagster daemon executed three five-minute sensor ticks",
                    "spark-thrift was continuously down in a real Docker runtime",
                    "the emitted recovery RunRequest was committed as a real Run",
                    "the recovery run materialized 9/9 marts",
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
