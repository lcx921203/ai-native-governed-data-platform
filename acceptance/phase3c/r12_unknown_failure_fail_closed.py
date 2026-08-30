#!/usr/bin/env python3
"""R12 Definition Runtime: ambiguous failures fail closed at both retry layers.

This harness proves two independent contracts with production code:

A1. Execution-adapter ambiguity
    A Docker/Spark command is available and ``spark-thrift`` reports healthy, but the
    command exits non-zero without timeout or stronger structured evidence. The
    production ``SparkComposeResource`` must classify the failure as ``unknown`` and,
    even under a job-level ``RetryPolicy(max_retries=2)``, execute the asset only once.

A2. Cross-run recovery ambiguity
    A failed exact-partition Daily Run is persisted with ``failure_class=unknown``.
    The production State Reader, Recovery Policy, and SensorDefinition must return
    ``ALERT_MANUAL / unknown_failure_class`` and emit no automatic Recovery RunRequest.

The harness deliberately does not infer root cause from stdout/stderr text. It also does
not prove a real Docker fault, real daemon alert delivery, or operator remediation.
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
from commerce_dagster.recovery_policy import RecoveryAction, decide_recovery
from commerce_dagster.recovery_state_current import collect_partition_recovery_state
from commerce_dagster.resources import SparkComposeResource
from commerce_dagster.sensors import shopify_daily_recovery_sensor


FIXED_NOW = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
PARTITION_KEY = "2026-08-05"
PROBE_JOB_NAME = "r12_unknown_failure_probe_job"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _completed(command, returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout)


def _run_unknown_adapter_probe(
    instance: dg.DagsterInstance,
    project_dir: Path,
) -> dict:
    counters = {"asset_attempts": 0, "spark_exec_attempts": 0, "health_checks": 0}

    def fake_subprocess_run(command, **_kwargs):
        command = list(command)
        if command[:4] == ["docker", "compose", "exec", "-T"]:
            counters["spark_exec_attempts"] += 1
            return _completed(
                command,
                17,
                "ambiguous command failure; no replay-safe structured cause",
            )
        if command[:3] == ["docker", "compose", "ps"]:
            counters["health_checks"] += 1
            return _completed(command, 0, "rustfs\npolaris\nspark-thrift\n")
        raise AssertionError(f"Unexpected subprocess command in R12 probe: {command!r}")

    @dg.asset
    def r12_unknown_adapter_probe(
        context: dg.AssetExecutionContext,
        spark: SparkComposeResource,
    ):
        counters["asset_attempts"] += 1
        spark.spark_submit(
            "acceptance/phase3c/r12_probe_never_executes.py",
            context,
        )
        return dg.MaterializeResult()

    probe_job = dg.define_asset_job(
        name=PROBE_JOB_NAME,
        selection=dg.AssetSelection.assets(r12_unknown_adapter_probe),
        # Deliberately permissive at the job layer. Production Failure(...,
        # allow_retries=False) for UNKNOWN must bypass this policy.
        op_retry_policy=dg.RetryPolicy(max_retries=2, delay=0),
        run_tags={"commerce/acceptance": "r12-unknown-failure-probe"},
    )
    probe_defs = dg.Definitions(
        assets=[r12_unknown_adapter_probe],
        jobs=[probe_job],
        resources={"spark": SparkComposeResource(project_dir=str(project_dir))},
    )

    with patch.object(resource_module.subprocess, "run", side_effect=fake_subprocess_run):
        result = probe_defs.get_job_def(PROBE_JOB_NAME).execute_in_process(
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
        "ambiguous_nonzero_classified_unknown": (
            tags.get(FAILURE_CLASS_TAG) == FailureClass.UNKNOWN.value
        ),
        "classification_source_is_execution_adapter": (
            tags.get(FAILURE_CLASS_SOURCE_TAG)
            == FailureClassSource.EXECUTION_ADAPTER.value
        ),
        "failure_component_is_spark_thrift": (
            tags.get(FAILURE_COMPONENT_TAG) == "spark-thrift"
        ),
        "unknown_disables_step_retry": counters["asset_attempts"] == 1,
        "only_one_spark_exec_attempt": counters["spark_exec_attempts"] == 1,
        "service_was_observed_running": counters["health_checks"] >= 1,
        "execute_in_process_result_failed": not result.success,
    }
    return {
        "probe_job_name": PROBE_JOB_NAME,
        "run_id": run.run_id if run else None,
        "run_status": run.status.value if run else None,
        "run_tags": tags,
        "asset_attempts": counters["asset_attempts"],
        "spark_exec_attempts": counters["spark_exec_attempts"],
        "health_checks": counters["health_checks"],
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def _seed_unknown_failed_daily_run(instance: dg.DagsterInstance) -> dg.DagsterRun:
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
            FailureClass.UNKNOWN,
            source=FailureClassSource.EXECUTION_ADAPTER,
            component="spark-thrift",
            reason_code="r12_seeded_ambiguous_nonzero",
        ),
    }
    run = instance.create_run_for_job(job_def=seed_daily_job, tags=tags)
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


def _evaluate_unknown_recovery_guard(instance: dg.DagsterInstance) -> dict:
    failed_run = _seed_unknown_failed_daily_run(instance)
    before_runs = _partition_runs(instance)

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

    after_runs = _partition_runs(instance)
    conditions = {
        "seeded_daily_run_is_failed": (
            failed_run.status is dg.DagsterRunStatus.FAILURE
        ),
        "state_reader_keeps_unknown_class": (
            state.observation.failure_class is FailureClass.UNKNOWN
        ),
        "policy_fails_closed_manual": (
            decision.action is RecoveryAction.ALERT_MANUAL
            and decision.reason_code == "unknown_failure_class"
        ),
        "sensor_emits_skip_reason": isinstance(sensor_result, dg.SkipReason),
        "sensor_does_not_emit_run_request": not isinstance(sensor_result, dg.RunRequest),
        "partition_run_count_unchanged": len(before_runs) == len(after_runs),
        "auto_replay_budget_remains_zero": state.observation.auto_replay_attempts == 0,
    }
    return {
        "failed_run_id": failed_run.run_id,
        "failure_class": state.observation.failure_class.value,
        "decision": {
            "action": decision.action.value,
            "reason_code": decision.reason_code,
        },
        "sensor_result_type": type(sensor_result).__name__,
        "partition_run_count_before": len(before_runs),
        "partition_run_count_after": len(after_runs),
        "auto_replay_attempts": state.observation.auto_replay_attempts,
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def main() -> int:
    args = parse_args()
    project_dir = Path(__file__).resolve().parents[2]

    with tempfile.TemporaryDirectory(prefix="commerce-r12-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            adapter_probe = _run_unknown_adapter_probe(instance, project_dir)
            recovery_guard = _evaluate_unknown_recovery_guard(instance)

            passed = (
                adapter_probe["result"] == "PASS"
                and recovery_guard["result"] == "PASS"
            )
            payload = {
                "scenario": "R12-A",
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "partition_key": PARTITION_KEY,
                "result": "PASS" if passed else "FAIL",
                "unknown_adapter_probe": adapter_probe,
                "unknown_recovery_guard": recovery_guard,
                "evidence_level": "C1-local-dagster-unknown-fail-closed-runtime",
                "does_not_prove": [
                    "a real Docker/Spark ambiguous production fault occurred",
                    "a real Dagster daemon delivered an external incident alert",
                    "the operator identified or repaired the unknown root cause",
                    "the partition later completed 9/9 consumer Marts",
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
