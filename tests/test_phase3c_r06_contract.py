"""Static/pure contracts for R06 deterministic dbt project/code failures."""

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


class R06ClassificationContractTest(unittest.TestCase):
    def test_parse_failure_is_deterministic_project_code(self) -> None:
        result = classify_dbt_failure(
            DbtFailureObservation(
                command_name="parse",
                command_succeeded=False,
                run_results=None,
            )
        )
        self.assertIs(result.failure_class, FailureClass.DETERMINISTIC_CODE)
        self.assertIs(result.source, FailureClassSource.DBT_COMMAND)
        self.assertEqual(result.reason_code, "dbt_parse_failed")

    def test_compile_failure_without_structured_proof_is_not_overclassified(self) -> None:
        result = classify_dbt_failure(
            DbtFailureObservation(
                command_name="compile",
                command_succeeded=False,
                run_results=None,
            )
        )
        self.assertIs(result.failure_class, FailureClass.UNKNOWN)
        self.assertIs(result.source, FailureClassSource.DBT_COMMAND)
        self.assertEqual(
            result.reason_code,
            "dbt_compile_failed_without_deterministic_evidence",
        )

    def test_deterministic_code_disables_step_retry(self) -> None:
        self.assertFalse(allow_step_retry(FailureClass.DETERMINISTIC_CODE))

    def test_deterministic_code_requires_manual_cross_run_recovery(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=False,
                failed_run=True,
                failure_class=FailureClass.DETERMINISTIC_CODE,
                infrastructure_healthy=True,
            )
        )
        self.assertIs(decision.action, RecoveryAction.ALERT_MANUAL)
        self.assertEqual(decision.reason_code, "deterministic_code_failure")


class R06SourceContractTest(unittest.TestCase):
    def test_acceptance_probe_defaults_to_valid_project(self) -> None:
        path = ROOT / "dbt/mercaso_dbt/models/acceptance/r06_deterministic_code_probe.sql"
        text = path.read_text(encoding="utf-8")
        self.assertIn("phase3c_r06_acceptance", text)
        self.assertIn("var('phase3c_r06_force_parse_failure', false)", text)
        self.assertIn("exceptions.raise_compiler_error", text)
        self.assertIn("R06_FORCED_DETERMINISTIC_CODE_FAILURE", text)
        self.assertIn("select", text.lower())

    def test_runtime_harness_uses_parse_and_production_adapter(self) -> None:
        path = ROOT / "acceptance/phase3c/r06_deterministic_code_failure.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("execute_classified_dbt", text)
        self.assertIn('"parse"', text)
        self.assertIn('"--no-partial-parse"', text)
        self.assertIn("phase3c_r06_force_parse_failure", text)
        self.assertIn("DbtCliResource", text)
        self.assertIn("asset_attempts", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("deterministic_code_failure", text)
        self.assertIn("sensor_does_not_emit_run_request", text)

    def test_classifier_documents_compile_infrastructure_boundary(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/failure_classification.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn('if command == "parse"', text)
        self.assertIn('if command == "compile" and observation.run_results is None', text)
        self.assertIn("compile 可能需要 Warehouse 连接", text)


if __name__ == "__main__":
    unittest.main()
