#!/usr/bin/env python3
"""R13 Definition Runtime: timeout -> bounded step retry -> one cross-run replay.

This harness proves two independent contracts with production code:

A1. A Docker/Spark command times out while ``spark-thrift`` still reports Running.
    ``SparkComposeResource`` must classify the failure as ``transient_runtime`` and,
    under the production retry budget (max_retries=2, zero delay in the harness),
    perform three total attempts before the Dagster Run ends in FAILURE.

A2. A failed exact-partition Daily Run persisted with
    ``failure_class=transient_runtime`` is evaluated after the Freshness Deadline while
    current runtime health is healthy. The production State Reader, Recovery Policy,
    and SensorDefinition must authorize exactly one bounded recovery RunRequest for the
    same partition with attempt-1 identity.

The harness mocks only the external subprocess timeout/current runtime health. It does
not prove a real Spark timeout, real daemon Run creation, or 9/9 Mart completion.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import dagster as dg

from commerce_dagster import resources as resource_module
from commerce_dagster import sensors as sensor_module
from commerce_dagster.automation_policy import (
    SHOPIFY_DAILY_JOB_NAME,
    SHOPIFY_DAILY_PARTITION_TAG,
)
from commerce_dagster.failure_classification import (
    FAILURE_CLASS_SOURCE_TAG,
    FAILURE_CLASS_TAG,
    FAILURE_COMPONENT_TAG,
    FailureClass,
    FailureClassSource,
    failure_class_tags,
)
from commerce_dagster.jobs import TRANSIENT_RETRY_POLICY
from commerce_dagster.recovery_policy import RecoveryAction, decide_recovery
from commerce_dagster.recovery_state_current import (
    AUTO_RECOVERY_TAG_VALUE,
    RECOVERY_ATTEMPT_TAG,
    RECOVERY_REASON_TAG,
    RECOVERY_TAG,
    collect_partition_recovery_state,
)
from commerce_dagster.resources import SparkComposeResource
from commerce_dagster.sensors import shopify_daily_recovery_sensor


FIXED_NOW = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
PARTITION_KEY = "2026-08-05"
EXPECTED_RECOVERY_RUN_KEY = "shopify-daily-recovery:2026-08-05:attempt-1"
PROBE_JOB_NAME = "r13_transient_timeout_probe_job"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _completed(command, returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout)


def _run_timeout_retry_probe(instance: dg.DagsterInstance, project_dir: Path) -> dict:
    counters = {
        "asset_attempts": 0,
        "spark_exec_attempts": 0,
        "service_health_checks": 0,
    }

    def fake_subprocess_run(command, **_kwargs):
        command = list(command)
        if command[:4] == ["docker", "compose", "exec", "-T"]:
            counters["spark_exec_attempts"] += 1
            raise subprocess.TimeoutExpired(command, timeout=1)
        if command[:3] == ["docker", "compose", "ps"]:
            counters["service_health_checks"] += 1
            return _completed(command, 0, "rustfs\npolaris\nspark-thrift\n")
        raise AssertionError(f"Unexpected subprocess command in R13 probe: {command!r}")

    @dg.asset
    def r13_spark_timeout_probe(
        context: dg.AssetExecutionContext,
        spark: SparkComposeResource,
    ):
        counters["asset_attempts"] += 1
        spark.spark_submit(
            "acceptance/phase3c/r13_probe_never_executes.py",
            context,
        )
        return dg.MaterializeResult()

    fast_retry_policy = dg.RetryPolicy(
        max_retries=TRANSIENT_RETRY_POLICY.max_retries,
        delay=0,
    )
    probe_job = dg.define_asset_job(
        name=PROBE_JOB_NAME,
        selection=dg.AssetSelection.assets(r13_spark_timeout_probe),
        op_retry_policy=fast_retry_policy,
        run_tags={"commerce/acceptance": "r13-transient-timeout-retry-probe"},
    )
    probe_defs = dg.Definitions(
        assets=[r13_spark_timeout_probe],
        jobs=[probe_job],
        resources={
            "spark": SparkComposeResource(
                project_dir=str(project_dir),
                command_timeout_seconds=1,
            )
        },
    )
    resolved_job = probe_defs.get_job_def(PROBE_JOB_NAME)

    with patch.object(resource_module.subprocess, "run", side_effect=fake_subprocess_run):
        result = resolved_job.execute_in_process(
            instance=instance,
            raise_on_error=False,
        )

    records = instance.get_run_records(
        filters=dg.RunsFilter(job_name=PROBE_JOB_NAME),
        limit=5,
    )
    run = records[0].dagster_run if records else None
    tags = dict(run.tags) if run else {}
    conditions = {
        "probe_run_failed": bool(run and run.status is dg.DagsterRunStatus.FAILURE),
        "max_retries_is_two": TRANSIENT_RETRY_POLICY.max_retries == 2,
        "three_total_asset_attempts": counters["asset_attempts"] == 3,
        "three_total_spark_exec_attempts": counters["spark_exec_attempts"] == 3,
        "service_health_checked_each_timeout": counters["service_health_checks"] == 3,
        "failure_class_is_transient_runtime": (
            tags.get(FAILURE_CLASS_TAG) == FailureClass.TRANSIENT_RUNTIME.value
        ),
        "failure_source_is_execution_adapter": (
            tags.get(FAILURE_CLASS_SOURCE_TAG)
            == FailureClassSource.EXECUTION_ADAPTER.value
        ),
        "failure_component_is_spark_thrift": (
            tags.get(FAILURE_COMPONENT_TAG) == "spark-thrift"
        ),
        "execute_in_process_result_failed": not result.success,
    }
    return {
        "probe_job_name": PROBE_JOB_NAME,
        "run_id": run.run_id if run else None,
        "run_status": run.status.value if run else None,
        "run_tags": tags,
        "asset_attempts": counters["asset_attempts"],
        "spark_exec_attempts": counters["spark_exec_attempts"],
        "service_health_checks": counters["service_health_checks"],
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def _seed_transient_failed_daily_run(instance: dg.DagsterInstance) -> dg.DagsterRun:
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
            FailureClass.TRANSIENT_RUNTIME,
            source=FailureClassSource.EXECUTION_ADAPTER,
            component="spark-thrift",
            reason_code="r13_seeded_command_timeout",
        ),
    }
    run = instance.create_run_for_job(job_def=seed_daily_job, tags=tags)
    instance.report_run_failed(run)
    refreshed = instance.get_run_by_id(run.run_id)
    assert refreshed is not None
    return refreshed


def _evaluate_transient_recovery(instance: dg.DagsterInstance) -> dict:
    failed_run = _seed_transient_failed_daily_run(instance)

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
            patch.object(sensor_module, "utc_now", return_value=FIXED_NOW),
            patch.object(
                sensor_module,
                "docker_compose_services_running",
                return_value=True,
            ),
        ):
            sensor_result = shopify_daily_recovery_sensor(context)

    is_request = isinstance(sensor_result, dg.RunRequest)
    conditions = {
        "seeded_daily_run_is_failed": (
            failed_run.status is dg.DagsterRunStatus.FAILURE
        ),
        "state_reader_recovers_transient_class": (
            state.observation.failure_class is FailureClass.TRANSIENT_RUNTIME
        ),
        "runtime_is_currently_healthy": state.observation.infrastructure_healthy,
        "replay_budget_is_zero_before_recovery": (
            state.observation.auto_replay_attempts == 0
        ),
        "policy_allows_one_auto_replay": (
            decision.action is RecoveryAction.AUTO_REPLAY
            and decision.reason_code
            == "transient_failure_after_runtime_recovered"
        ),
        "sensor_emits_run_request": is_request,
        "recovery_targets_exact_partition": (
            is_request and sensor_result.partition_key == PARTITION_KEY
        ),
        "recovery_run_key_is_attempt_one": (
            is_request and sensor_result.run_key == EXPECTED_RECOVERY_RUN_KEY
        ),
        "recovery_origin_tag": (
            is_request
            and sensor_result.tags.get("commerce/automation") == "recovery-sensor"
        ),
        "recovery_auto_tag": (
            is_request
            and sensor_result.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
        ),
        "recovery_attempt_is_one": (
            is_request and sensor_result.tags.get(RECOVERY_ATTEMPT_TAG) == "1"
        ),
        "recovery_reason_matches_transient_restore": (
            is_request
            and sensor_result.tags.get(RECOVERY_REASON_TAG)
            == "transient_failure_after_runtime_recovered"
        ),
    }
    return {
        "failed_run_id": failed_run.run_id,
        "partition_key": PARTITION_KEY,
        "failure_class": state.observation.failure_class.value,
        "decision": {
            "action": decision.action.value,
            "reason_code": decision.reason_code,
        },
        "sensor_result_type": type(sensor_result).__name__,
        "run_key": sensor_result.run_key if is_request else None,
        "tags": dict(sensor_result.tags) if is_request else {},
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def main() -> int:
    args = parse_args()
    project_dir = Path(__file__).resolve().parents[2]

    with tempfile.TemporaryDirectory(prefix="commerce-r13-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            timeout_probe = _run_timeout_retry_probe(instance, project_dir)
            recovery_transition = _evaluate_transient_recovery(instance)

            passed = (
                timeout_probe["result"] == "PASS"
                and recovery_transition["result"] == "PASS"
            )
            payload = {
                "scenario": "R13-A",
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "partition_key": PARTITION_KEY,
                "result": "PASS" if passed else "FAIL",
                "timeout_retry_probe": timeout_probe,
                "recovery_transition": recovery_transition,
                "evidence_level": "C1-local-dagster-transient-runtime",
                "does_not_prove": [
                    "a real Spark/Docker command timed out in the data plane",
                    "the production backoff delays elapsed in wall-clock time",
                    "a real Dagster daemon committed the recovery RunRequest",
                    "the recovery run materialized 9/9 consumer Marts",
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
