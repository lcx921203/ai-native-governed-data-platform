#!/usr/bin/env python3
"""R06 Runtime Acceptance: deterministic dbt project/code failure must fail closed.

This harness separates two proofs:

A1. Run a real `dbt parse` failure through the production `execute_classified_dbt`
    adapter. The acceptance-only model is valid by default; this harness enables a var
    that calls dbt's `exceptions.raise_compiler_error`. `dbt parse` is deliberately used
    because it does not connect to the warehouse, so the failure is not confounded with
    Spark/adapter availability. A job-level retry policy is present to prove that
    `deterministic_code` disables Step Retry.

A2. Seed the exact failed-partition evidence into a persistent temporary Dagster
    instance, invoke the production state reader / recovery policy / SensorDefinition,
    and prove deterministic code failure requires manual remediation and emits no
    automatic replay request.

A1 requires Dagster + dbt packages but no live warehouse connection. A2 requires Dagster
only. Neither proves that an operator committed a code fix or that a later corrected run
materialized the partition successfully.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import dagster as dg
from dagster_dbt import DbtCliResource

from commerce_dagster import sensors as sensor_module
from commerce_dagster.automation_policy import (
    SHOPIFY_DAILY_JOB_NAME,
    SHOPIFY_DAILY_PARTITION_TAG,
)
from commerce_dagster.dbt_failure_adapter import execute_classified_dbt
from commerce_dagster.failure_classification import (
    FAILURE_CLASS_SOURCE_TAG,
    FAILURE_CLASS_TAG,
    FAILURE_COMPONENT_TAG,
    FAILURE_REASON_TAG,
    FailureClass,
    FailureClassSource,
    failure_class_tags,
)
from commerce_dagster.project import DBT_PROFILES_DIR, DBT_PROJECT_DIR
from commerce_dagster.recovery_policy import RecoveryAction, decide_recovery
from commerce_dagster.recovery_state_current import collect_partition_recovery_state
from commerce_dagster.sensors import shopify_daily_recovery_sensor


FIXED_NOW = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
PARTITION_KEY = "2026-08-05"
PROBE_JOB_NAME = "r06_deterministic_code_probe_job"
FORCE_VAR = "phase3c_r06_force_parse_failure"
FORCED_ERROR_MARKER = "R06_FORCED_DETERMINISTIC_CODE_FAILURE"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-dbt-runtime",
        action="store_true",
        help="Run only the persistent-Dagster recovery-policy proof (R06-A2).",
    )
    return parser.parse_args()


def _run_dbt_parse_probe(instance: dg.DagsterInstance) -> dict:
    counters = {"asset_attempts": 0}

    @dg.asset
    def r06_dbt_parse_probe(
        context: dg.AssetExecutionContext,
        dbt: DbtCliResource,
    ):
        counters["asset_attempts"] += 1
        args = [
            "parse",
            "--no-partial-parse",
            "--vars",
            json.dumps({FORCE_VAR: True}),
        ]
        yield from execute_classified_dbt(context=context, dbt=dbt, args=args)

    # Deliberately allow retries at the job level. The production adapter must raise
    # Failure(..., allow_retries=False) for deterministic project/code errors.
    probe_job = dg.define_asset_job(
        name=PROBE_JOB_NAME,
        selection=dg.AssetSelection.assets(r06_dbt_parse_probe),
        op_retry_policy=dg.RetryPolicy(max_retries=2, delay=0),
        run_tags={"commerce/acceptance": "r06-deterministic-code-probe"},
    )
    probe_defs = dg.Definitions(
        assets=[r06_dbt_parse_probe],
        jobs=[probe_job],
        resources={
            "dbt": DbtCliResource(
                project_dir=str(DBT_PROJECT_DIR),
                profiles_dir=str(DBT_PROFILES_DIR),
            )
        },
    )

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
        "failure_class_is_deterministic_code": (
            tags.get(FAILURE_CLASS_TAG) == FailureClass.DETERMINISTIC_CODE.value
        ),
        "failure_source_is_dbt_command": (
            tags.get(FAILURE_CLASS_SOURCE_TAG) == FailureClassSource.DBT_COMMAND.value
        ),
        "failure_component_is_dbt_parse": (
            tags.get(FAILURE_COMPONENT_TAG) == "dbt:parse"
        ),
        "failure_reason_is_parse_failed": (
            tags.get(FAILURE_REASON_TAG) == "dbt_parse_failed"
        ),
        "deterministic_code_disables_step_retry": counters["asset_attempts"] == 1,
        "execute_in_process_result_failed": not result.success,
    }
    return {
        "probe_job_name": PROBE_JOB_NAME,
        "run_id": run.run_id if run else None,
        "run_status": run.status.value if run else None,
        "run_tags": tags,
        "asset_attempts": counters["asset_attempts"],
        "forced_error_marker": FORCED_ERROR_MARKER,
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
            FailureClass.DETERMINISTIC_CODE,
            source=FailureClassSource.DBT_COMMAND,
            component="dbt:parse",
            reason_code="dbt_parse_failed",
        ),
    }
    run = instance.create_run_for_job(job_def=seed_daily_job, tags=tags)
    instance.report_run_failed(run)
    return instance.get_run_by_id(run.run_id)


def _evaluate_manual_recovery_guard(instance: dg.DagsterInstance) -> dict:
    failed_run = _seed_failed_daily_run(instance)
    before_records = instance.get_run_records(
        filters=dg.RunsFilter(
            job_name=SHOPIFY_DAILY_JOB_NAME,
            tags={SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY},
        ),
        limit=50,
    )
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

    after_records = instance.get_run_records(
        filters=dg.RunsFilter(
            job_name=SHOPIFY_DAILY_JOB_NAME,
            tags={SHOPIFY_DAILY_PARTITION_TAG: PARTITION_KEY},
        ),
        limit=50,
    )

    conditions = {
        "seeded_daily_run_is_failed": (
            failed_run is not None and failed_run.status is dg.DagsterRunStatus.FAILURE
        ),
        "state_reader_recovers_deterministic_code_class": (
            state.observation.failure_class is FailureClass.DETERMINISTIC_CODE
        ),
        "policy_requires_manual_action": (
            decision.action is RecoveryAction.ALERT_MANUAL
            and decision.reason_code == "deterministic_code_failure"
        ),
        "sensor_emits_skip_reason": isinstance(sensor_result, dg.SkipReason),
        "sensor_does_not_emit_run_request": not isinstance(sensor_result, dg.RunRequest),
        "partition_run_count_unchanged": len(before_records) == len(after_records),
        "auto_replay_budget_remains_zero": state.observation.auto_replay_attempts == 0,
    }
    return {
        "failed_run_id": failed_run.run_id if failed_run else None,
        "failure_class": state.observation.failure_class.value,
        "decision": {
            "action": decision.action.value,
            "reason_code": decision.reason_code,
        },
        "sensor_result_type": type(sensor_result).__name__,
        "partition_run_count_before": len(before_records),
        "partition_run_count_after": len(after_records),
        "auto_replay_attempts": state.observation.auto_replay_attempts,
        "conditions": conditions,
        "result": "PASS" if all(conditions.values()) else "FAIL",
    }


def main() -> int:
    args = parse_args()

    with tempfile.TemporaryDirectory(prefix="commerce-r06-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            parse_probe = (
                {"result": "SKIPPED", "reason": "--skip-dbt-runtime"}
                if args.skip_dbt_runtime
                else _run_dbt_parse_probe(instance)
            )
            recovery_guard = _evaluate_manual_recovery_guard(instance)

            passed = (
                recovery_guard["result"] == "PASS"
                and (args.skip_dbt_runtime or parse_probe["result"] == "PASS")
            )
            payload = {
                "scenario": "R06-A",
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "partition_key": PARTITION_KEY,
                "result": "PASS" if passed else "FAIL",
                "dbt_parse_probe": parse_probe,
                "manual_recovery_guard": recovery_guard,
                "evidence_level": "C1-local-dagster-plus-dbt-parse-runtime",
                "does_not_prove": [
                    "a naturally occurring production code defect caused the daily run",
                    "an operator committed or deployed the corrective code change",
                    "the corrected partition was later replayed successfully",
                    "external alert delivery reached an operator",
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
