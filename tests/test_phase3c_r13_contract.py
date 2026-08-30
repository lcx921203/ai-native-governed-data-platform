"""Static/pure contracts for R13 transient-runtime timeout recovery."""

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


class R13PolicyContractTest(unittest.TestCase):
    def test_timeout_with_running_service_is_transient_runtime(self) -> None:
        result = classify_command_failure(
            CommandFailureObservation(
                command_available=True,
                timed_out=True,
                service_running=True,
            )
        )
        self.assertIs(result, FailureClass.TRANSIENT_RUNTIME)

    def test_transient_runtime_is_step_retry_safe(self) -> None:
        self.assertTrue(allow_step_retry(FailureClass.TRANSIENT_RUNTIME))

    def test_transient_failure_after_runtime_recovered_allows_one_replay(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=False,
                failed_run=True,
                failure_class=FailureClass.TRANSIENT_RUNTIME,
                infrastructure_healthy=True,
                auto_replay_attempts=0,
            )
        )
        self.assertIs(decision.action, RecoveryAction.AUTO_REPLAY)
        self.assertEqual(
            decision.reason_code,
            "transient_failure_after_runtime_recovered",
        )

    def test_replay_budget_still_bounds_transient_failure(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=False,
                failed_run=True,
                failure_class=FailureClass.TRANSIENT_RUNTIME,
                infrastructure_healthy=True,
                auto_replay_attempts=1,
            )
        )
        self.assertIs(decision.action, RecoveryAction.ALERT_MANUAL)
        self.assertEqual(decision.reason_code, "auto_replay_budget_exhausted")


class R13SourceContractTest(unittest.TestCase):
    def test_production_resource_classifies_timeouts_with_current_service_health(self) -> None:
        path = PKG / "resources.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("except subprocess.TimeoutExpired", text)
        self.assertIn("timed_out=True", text)
        self.assertIn("service_running=self._service_running()", text)
        self.assertIn("allow_retries=allow_step_retry(failure_class)", text)

    def test_r13_harness_proves_retry_count_and_sensor_recovery_without_overclaim(self) -> None:
        path = ROOT / "acceptance/phase3c/r13_transient_runtime_recovery.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("subprocess.TimeoutExpired", text)
        self.assertIn("spark-thrift\\n", text)
        self.assertIn("TRANSIENT_RETRY_POLICY.max_retries", text)
        self.assertIn("three_total_spark_exec_attempts", text)
        self.assertIn("FailureClass.TRANSIENT_RUNTIME", text)
        self.assertIn("transient_failure_after_runtime_recovered", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("EXPECTED_RECOVERY_RUN_KEY", text)
        self.assertIn("does_not_prove", text)
        self.assertIn("real Spark/Docker command timed out", text)


if __name__ == "__main__":
    unittest.main()
