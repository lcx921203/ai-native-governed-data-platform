"""Static/pure contracts for R05 dbt data-contract failure handling."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "orchestration/dagster/commerce_dagster"
sys.path.insert(0, str(PKG))

from failure_classification import (  # noqa:E402
    DbtFailureObservation,
    FailureClass,
    FailureClassSource,
    allow_step_retry,
    classify_dbt_failure,
)
from recovery_policy import RecoveryAction, RecoveryObservation, decide_recovery  # noqa:E402


class R05ClassificationContractTest(unittest.TestCase):
    def test_mixed_dbt_results_classify_failed_test_as_data_contract(self) -> None:
        result = classify_dbt_failure(
            DbtFailureObservation(
                command_name="test",
                command_succeeded=False,
                run_results={
                    "results": [
                        {"unique_id": "model.mercaso_dbt.orders", "status": "success"},
                        {
                            "unique_id": "test.mercaso_dbt.r05_force_data_contract_failure",
                            "status": "fail",
                        },
                    ]
                },
            )
        )
        self.assertIs(result.failure_class, FailureClass.DATA_CONTRACT)
        self.assertIs(result.source, FailureClassSource.DBT_ARTIFACT)
        self.assertEqual(result.reason_code, "dbt_data_test_failed")
        self.assertEqual(
            result.failed_test_ids,
            ("test.mercaso_dbt.r05_force_data_contract_failure",),
        )

    def test_test_error_does_not_overclaim_data_contract(self) -> None:
        result = classify_dbt_failure(
            DbtFailureObservation(
                command_name="test",
                command_succeeded=False,
                run_results={
                    "results": [
                        {
                            "unique_id": "test.mercaso_dbt.r05_force_data_contract_failure",
                            "status": "error",
                        }
                    ]
                },
            )
        )
        self.assertIs(result.failure_class, FailureClass.UNKNOWN)
        self.assertEqual(result.reason_code, "dbt_nonzero_without_replay_safe_class")

    def test_data_contract_disables_step_retry(self) -> None:
        self.assertFalse(allow_step_retry(FailureClass.DATA_CONTRACT))

    def test_data_contract_requires_manual_cross_run_recovery(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=False,
                failed_run=True,
                failure_class=FailureClass.DATA_CONTRACT,
                infrastructure_healthy=True,
            )
        )
        self.assertIs(decision.action, RecoveryAction.ALERT_MANUAL)
        self.assertEqual(decision.reason_code, "data_contract_failure")


class R05SourceContractTest(unittest.TestCase):
    def test_acceptance_only_dbt_test_defaults_to_pass(self) -> None:
        path = ROOT / "dbt/mercaso_dbt/tests/acceptance/r05_force_data_contract_failure.sql"
        text = path.read_text(encoding="utf-8")
        self.assertIn("phase3c_r05_acceptance", text)
        self.assertIn("var('phase3c_r05_force_data_contract_failure', false)", text)
        self.assertIn("where 1 = 0", text)
        self.assertIn("R05_FORCED_DATA_CONTRACT_FAILURE", text)

    def test_runtime_harness_uses_production_dbt_adapter_and_sensor(self) -> None:
        path = ROOT / "acceptance/phase3c/r05_data_contract_failure.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("execute_classified_dbt", text)
        self.assertIn("DbtCliResource", text)
        self.assertIn("phase3c_r05_force_data_contract_failure", text)
        self.assertIn("run_results.json", text)
        self.assertIn("asset_attempts", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("data_contract_failure", text)
        self.assertIn("sensor_does_not_emit_run_request", text)

    def test_dbt_adapter_persists_structured_failure_before_raising(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/dbt_failure_adapter.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertLess(text.index("_record_dbt_failure_tags("), text.rindex("raise dg.Failure("))
        self.assertIn("allow_retries=allow_step_retry(classification.failure_class)", text)


if __name__ == "__main__":
    unittest.main()
