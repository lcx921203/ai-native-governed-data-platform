#!/usr/bin/env python3
"""R03 Definition Runtime: infrastructure outage -> bounded retry -> recovery decision.

This acceptance harness deliberately separates two proofs:

A1. Execute a tiny real Dagster asset through the production SparkComposeResource while
    Docker/Spark is simulated unavailable.  The probe uses the production retry count
    (max_retries=2) with zero delay so it proves three attempts, final Run FAILURE, and
    structured infrastructure_unavailable run tags without waiting for backoff delays.

A2. Seed one failed run record for the real daily job identity / exact partition, then
    invoke the production Recovery Sensor twice at a fixed post-deadline time:
      - current infrastructure unhealthy -> no RunRequest / ALERT_AND_WAIT
      - current infrastructure healthy   -> one exact-partition AUTO_REPLAY RunRequest

It does not prove a real Docker outage, daemon tick, or 9/9 data materialization.
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
PROBE_JOB_NAME = "r03_infrastructure_retry_probe_job"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _completed(command, returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout)


def _run_adapter_retry_probe(instance: dg.DagsterInstance, project_dir: Path) -> dict:
    counters = {"spark_exec_attempts": 0, "service_health_checks": 0}

    def fake_subprocess_run(command, **_kwargs):
        command = list(command)
        if command[:4] == ["docker", "compose", "exec", "-T"]:
            counters["spark_exec_attempts"] += 1
            return _completed(command, 1, "spark-thrift service is unavailable")
        if command[:3] == ["docker", "compose", "ps"]:
            counters["service_health_checks"] += 1
            # rustfs/polaris may still be up, but spark-thrift is intentionally absent.
            return _completed(command, 0, "rustfs\npolaris\n")
        raise AssertionError(f"Unexpected subprocess command in R03 probe: {command!r}")

    @dg.asset
    def r03_spark_adapter_probe(
        context: dg.AssetExecutionContext,
        spark: SparkComposeResource,
    ):
        spark.spark_submit(
            "acceptance/phase3c/r03_probe_never_executes.py",
            context,
        )
        return dg.MaterializeResult()

    fast_retry_policy = dg.RetryPolicy(
        max_retries=TRANSIENT_RETRY_POLICY.max_retries,
        delay=0,
    )
    probe_job = dg.define_asset_job(
        name=PROBE_JOB_NAME,
        selection=dg.AssetSelection.assets(r03_spark_adapter_probe),
        op_retry_policy=fast_retry_policy,
        run_tags={"commerce/acceptance": "r03-adapter-retry-probe"},
    )
    probe_defs = dg.Definitions(
        assets=[r03_spark_adapter_probe],
        jobs=[probe_job],
        resources={"spark": SparkComposeResource(project_dir=str(project_dir))},
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
        "three_total_adapter_attempts": counters["spark_exec_attempts"] == 3,
        "failure_class_is_infrastructure_unavailable": (
            tags.get(FAILURE_CLASS_TAG) == FailureClass.INFRASTRUCTURE_UNAVAILABLE.value
        ),
        "failure_source_is_execution_adapter": (
            tags.get(FAILURE_CLASS_SOURCE_TAG) == FailureClassSource.EXECUTION_ADAPTER.value
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
        "spark_exec_attempts": counters["spark_exec_attempts"],
        "service_health_checks": counters["service_health_checks"],
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


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
            reason_code="r03_seeded_infrastructure_outage",
        ),
    }
    run = instance.create_run_for_job(job_def=seed_daily_job, tags=tags)
    instance.report_run_failed(run)
    return instance.get_run_by_id(run.run_id)


def _evaluate_sensor_transition(instance: dg.DagsterInstance) -> dict:
    failed_run = _seed_failed_daily_run(instance)

    state_down = collect_partition_recovery_state(
        instance,
        partition_key=PARTITION_KEY,
        freshness_overdue=True,
        infrastructure_healthy=False,
        missed_schedule_eligible=True,
    )
    decision_down = decide_recovery(state_down.observation)

    with dg.build_sensor_context(instance=instance) as context:
        with (
            patch.object(sensor_module, "utc_now", return_value=FIXED_NOW),
            patch.object(
                sensor_module,
                "docker_compose_services_running",
                return_value=False,
            ),
        ):
            sensor_down = shopify_daily_recovery_sensor(context)

    state_recovered = collect_partition_recovery_state(
        instance,
        partition_key=PARTITION_KEY,
        freshness_overdue=True,
        infrastructure_healthy=True,
        missed_schedule_eligible=True,
    )
    decision_recovered = decide_recovery(state_recovered.observation)

    with dg.build_sensor_context(instance=instance) as context:
        with (
            patch.object(sensor_module, "utc_now", return_value=FIXED_NOW),
            patch.object(
                sensor_module,
                "docker_compose_services_running",
                return_value=True,
            ),
        ):
            sensor_recovered = shopify_daily_recovery_sensor(context)

    recovered_is_request = isinstance(sensor_recovered, dg.RunRequest)
    conditions = {
        "seeded_daily_run_is_failed": (
            failed_run is not None and failed_run.status is dg.DagsterRunStatus.FAILURE
        ),
        "state_reader_recovers_failure_class": (
            state_down.observation.failure_class
            is FailureClass.INFRASTRUCTURE_UNAVAILABLE
        ),
        "still_down_policy_alerts_and_waits": (
            decision_down.action is RecoveryAction.ALERT_AND_WAIT
            and decision_down.reason_code == "infrastructure_unhealthy"
        ),
        "still_down_sensor_creates_no_run_request": isinstance(sensor_down, dg.SkipReason),
        "restored_policy_allows_auto_replay": (
            decision_recovered.action is RecoveryAction.AUTO_REPLAY
            and decision_recovered.reason_code
            == "infrastructure_failure_after_runtime_recovered"
        ),
        "restored_sensor_emits_run_request": recovered_is_request,
        "recovery_targets_exact_partition": (
            recovered_is_request and sensor_recovered.partition_key == PARTITION_KEY
        ),
        "recovery_run_key_is_bounded_attempt_one": (
            recovered_is_request
            and sensor_recovered.run_key == EXPECTED_RECOVERY_RUN_KEY
        ),
        "recovery_origin_tag": (
            recovered_is_request
            and sensor_recovered.tags.get("commerce/automation") == "recovery-sensor"
        ),
        "recovery_auto_tag": (
            recovered_is_request
            and sensor_recovered.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
        ),
        "recovery_attempt_is_one": (
            recovered_is_request
            and sensor_recovered.tags.get(RECOVERY_ATTEMPT_TAG) == "1"
        ),
        "recovery_reason_matches_infra_restore": (
            recovered_is_request
            and sensor_recovered.tags.get(RECOVERY_REASON_TAG)
            == "infrastructure_failure_after_runtime_recovered"
        ),
    }
    return {
        "seeded_failed_run_id": failed_run.run_id if failed_run else None,
        "partition_key": PARTITION_KEY,
        "fixed_now_utc": FIXED_NOW.isoformat(),
        "still_down": {
            "policy_action": decision_down.action.value,
            "reason_code": decision_down.reason_code,
            "sensor_result_type": type(sensor_down).__name__,
        },
        "recovered": {
            "policy_action": decision_recovered.action.value,
            "reason_code": decision_recovered.reason_code,
            "sensor_result_type": type(sensor_recovered).__name__,
            "run_key": sensor_recovered.run_key if recovered_is_request else None,
            "tags": dict(sensor_recovered.tags) if recovered_is_request else {},
        },
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).resolve().parents[2]

    with tempfile.TemporaryDirectory(prefix="commerce-r03-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            adapter_probe = _run_adapter_retry_probe(instance, project_root)
            sensor_transition = _evaluate_sensor_transition(instance)

            passed = (
                adapter_probe["result"] == "PASS"
                and sensor_transition["result"] == "PASS"
            )
            payload = {
                "scenario": "R03-A",
                "result": "PASS" if passed else "FAIL",
                "adapter_retry_probe": adapter_probe,
                "sensor_transition": sensor_transition,
                "evidence_level": "C1-local-dagster-runtime",
                "does_not_prove": [
                    "spark-thrift was stopped in a real Docker runtime",
                    "the real daily job exhausted its production backoff timings",
                    "Dagster daemon committed the recovery RunRequest",
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
