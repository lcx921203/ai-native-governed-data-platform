#!/usr/bin/env python3
"""R05 Runtime Acceptance: dbt data-contract failure must fail closed.

This harness separates two proofs:

A1. Run a real dbt singular data test through the production
    ``execute_classified_dbt`` adapter. The test is acceptance-only and defaults to
    PASS; this harness explicitly enables a var that makes it return one violating row.
    A job-level retry policy is deliberately present, so the run also proves that a
    structured ``data_contract`` failure opts out of Step Retry.

A2. Seed the exact failed-partition evidence into a persistent temporary Dagster
    instance, invoke the production recovery state reader / policy / SensorDefinition,
    and prove that a data-contract failure produces ALERT_MANUAL / SkipReason rather
    than an automatic cross-run replay.

A1 requires a working dbt + Spark Thrift runtime. A2 requires Dagster only. Neither
proves external alert delivery or a human remediation workflow.
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
PROBE_JOB_NAME = "r05_data_contract_probe_job"
R05_TEST_NAME = "r05_force_data_contract_failure"
R05_TEST_TAG = "phase3c_r05_acceptance"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-dbt-runtime",
        action="store_true",
        help="Run only the persistent-Dagster recovery-policy proof (R05-A2).",
    )
    return parser.parse_args()


def _read_r05_run_result() -> dict | None:
    path = Path(DBT_PROJECT_DIR) / "target" / "run_results.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    for result in payload.get("results", []):
        unique_id = str(result.get("unique_id") or "")
        if R05_TEST_NAME in unique_id:
            return {
                "unique_id": unique_id,
                "status": str(result.get("status") or "").lower(),
                "failures": result.get("failures"),
                "message": result.get("message"),
            }
    return None


def _run_dbt_contract_probe(instance: dg.DagsterInstance) -> dict:
    counters = {"asset_attempts": 0}

    @dg.asset
    def r05_dbt_contract_probe(
        context: dg.AssetExecutionContext,
        dbt: DbtCliResource,
    ):
        counters["asset_attempts"] += 1
        args = [
            "test",
            "--select",
            f"tag:{R05_TEST_TAG}",
            "--vars",
            json.dumps({"phase3c_r05_force_data_contract_failure": True}),
        ]
        yield from execute_classified_dbt(context=context, dbt=dbt, args=args)

    # Deliberately permit retries at the job level.  The production adapter must still
    # raise Failure(..., allow_retries=False) for DATA_CONTRACT, so attempts stay at 1.
    probe_job = dg.define_asset_job(
        name=PROBE_JOB_NAME,
        selection=dg.AssetSelection.assets(r05_dbt_contract_probe),
        op_retry_policy=dg.RetryPolicy(max_retries=2, delay=0),
        run_tags={"commerce/acceptance": "r05-data-contract-probe"},
    )
    probe_defs = dg.Definitions(
        assets=[r05_dbt_contract_probe],
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
    dbt_test_result = _read_r05_run_result()

    conditions = {
        "dbt_test_result_exists": dbt_test_result is not None,
        "run_results_status_is_fail": (
            dbt_test_result is not None and dbt_test_result["status"] == "fail"
        ),
        "probe_run_failed": bool(run and run.status is dg.DagsterRunStatus.FAILURE),
        "failure_class_is_data_contract": (
            tags.get(FAILURE_CLASS_TAG) == FailureClass.DATA_CONTRACT.value
        ),
        "failure_source_is_dbt_artifact": (
            tags.get(FAILURE_CLASS_SOURCE_TAG) == FailureClassSource.DBT_ARTIFACT.value
        ),
        "failure_component_is_dbt_test": (
            tags.get(FAILURE_COMPONENT_TAG) == "dbt:test"
        ),
        "failure_reason_is_data_test_failed": (
            tags.get(FAILURE_REASON_TAG) == "dbt_data_test_failed"
        ),
        "data_contract_disables_step_retry": counters["asset_attempts"] == 1,
        "execute_in_process_result_failed": not result.success,
    }
    return {
        "probe_job_name": PROBE_JOB_NAME,
        "run_id": run.run_id if run else None,
        "run_status": run.status.value if run else None,
        "run_tags": tags,
        "asset_attempts": counters["asset_attempts"],
        "dbt_test_result": dbt_test_result,
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
            FailureClass.DATA_CONTRACT,
            source=FailureClassSource.DBT_ARTIFACT,
            component="dbt:test",
            reason_code="dbt_data_test_failed",
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
        "state_reader_recovers_data_contract_class": (
            state.observation.failure_class is FailureClass.DATA_CONTRACT
        ),
        "policy_requires_manual_action": (
            decision.action is RecoveryAction.ALERT_MANUAL
            and decision.reason_code == "data_contract_failure"
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

    with tempfile.TemporaryDirectory(prefix="commerce-r05-") as temp_dir:
        with dg.DagsterInstance.local_temp(temp_dir) as instance:
            dbt_probe = (
                {"result": "SKIPPED", "reason": "--skip-dbt-runtime"}
                if args.skip_dbt_runtime
                else _run_dbt_contract_probe(instance)
            )
            recovery_guard = _evaluate_manual_recovery_guard(instance)

            passed = (
                recovery_guard["result"] == "PASS"
                and (args.skip_dbt_runtime or dbt_probe["result"] == "PASS")
            )
            payload = {
                "scenario": "R05-A",
                "fixed_now_utc": FIXED_NOW.isoformat(),
                "partition_key": PARTITION_KEY,
                "result": "PASS" if passed else "FAIL",
                "dbt_contract_probe": dbt_probe,
                "manual_recovery_guard": recovery_guard,
                "evidence_level": "C1-local-dagster-plus-dbt-runtime",
                "does_not_prove": [
                    "the production daily partition hit a naturally occurring data defect",
                    "external alert delivery reached an operator",
                    "a human corrected/quarantined the bad business data",
                    "the corrected partition was later replayed successfully",
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
