"""Static/pure contracts for R12 unknown-failure fail-closed behavior."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "orchestration/dagster/commerce_dagster"
sys.path.insert(0, str(PKG))

from failure_classification import (  # noqa:E402
    CommandFailureObservation,
    FailureClass,
    allow_step_retry,
    classify_command_failure,
)
from recovery_policy import RecoveryAction, RecoveryObservation, decide_recovery  # noqa:E402


class R12PolicyContractTest(unittest.TestCase):
    def test_ambiguous_command_nonzero_stays_unknown(self) -> None:
        result = classify_command_failure(
            CommandFailureObservation(
                command_available=True,
                timed_out=False,
                service_running=True,
                return_code=17,
            )
        )
        self.assertIs(result, FailureClass.UNKNOWN)

    def test_unknown_disables_step_retry(self) -> None:
        self.assertFalse(allow_step_retry(FailureClass.UNKNOWN))

    def test_only_proven_transient_or_infrastructure_classes_get_step_retry(self) -> None:
        self.assertTrue(allow_step_retry(FailureClass.TRANSIENT_RUNTIME))
        self.assertTrue(allow_step_retry(FailureClass.INFRASTRUCTURE_UNAVAILABLE))
        self.assertFalse(allow_step_retry(FailureClass.DETERMINISTIC_CODE))
        self.assertFalse(allow_step_retry(FailureClass.DATA_CONTRACT))
        self.assertFalse(allow_step_retry(FailureClass.NONE))

    def test_unknown_requires_manual_cross_run_recovery(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=False,
                failed_run=True,
                failure_class=FailureClass.UNKNOWN,
                infrastructure_healthy=True,
            )
        )
        self.assertIs(decision.action, RecoveryAction.ALERT_MANUAL)
        self.assertEqual(decision.reason_code, "unknown_failure_class")


class R12SourceContractTest(unittest.TestCase):
    def test_retry_whitelist_does_not_contain_unknown(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/failure_classification.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "allow_step_retry"
        )
        rendered = ast.unparse(function)
        self.assertIn("FailureClass.TRANSIENT_RUNTIME", rendered)
        self.assertIn("FailureClass.INFRASTRUCTURE_UNAVAILABLE", rendered)
        self.assertNotIn("FailureClass.UNKNOWN", rendered)

    def test_state_reader_maps_missing_or_invalid_failure_tags_to_unknown(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_state.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("if raw_value is None", text)
        self.assertIn("return FailureClass.UNKNOWN", text)
        self.assertIn("except ValueError", text)

    def test_runtime_harness_uses_production_adapter_and_sensor_without_overclaim(self) -> None:
        path = ROOT / "acceptance/phase3c/r12_unknown_failure_fail_closed.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("SparkComposeResource", text)
        self.assertIn("ambiguous command failure", text)
        self.assertIn("dg.RetryPolicy(max_retries=2, delay=0)", text)
        self.assertIn("unknown_disables_step_retry", text)
        self.assertIn("asset_attempts", text)
        self.assertIn("FailureClass.UNKNOWN", text)
        self.assertIn("unknown_failure_class", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("sensor_does_not_emit_run_request", text)
        self.assertIn("does_not_prove", text)
        self.assertIn("real Docker/Spark", text)


if __name__ == "__main__":
    unittest.main()
