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
    classify_command_failure,
)
from recovery_policy import (  # noqa:E402
    RecoveryAction,
    RecoveryObservation,
    decide_recovery,
)


class Phase3CR03ContractTest(unittest.TestCase):
    def test_stopped_service_classifies_as_infrastructure_unavailable(self):
        failure_class = classify_command_failure(
            CommandFailureObservation(
                command_available=True,
                service_running=False,
                return_code=1,
            )
        )
        self.assertEqual(failure_class, FailureClass.INFRASTRUCTURE_UNAVAILABLE)

    def test_infrastructure_still_down_alerts_and_waits(self):
        decision = decide_recovery(
            RecoveryObservation(
                "2026-08-05",
                True,
                False,
                False,
                True,
                failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE,
                infrastructure_healthy=False,
            )
        )
        self.assertEqual(decision.action, RecoveryAction.ALERT_AND_WAIT)
        self.assertEqual(decision.reason_code, "infrastructure_unhealthy")

    def test_infrastructure_recovered_allows_one_replay(self):
        decision = decide_recovery(
            RecoveryObservation(
                "2026-08-05",
                True,
                False,
                False,
                True,
                failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE,
                infrastructure_healthy=True,
                auto_replay_attempts=0,
            )
        )
        self.assertEqual(decision.action, RecoveryAction.AUTO_REPLAY)
        self.assertEqual(
            decision.reason_code,
            "infrastructure_failure_after_runtime_recovered",
        )

    def test_production_resource_persists_structured_failure_tags(self):
        text = (PKG / "resources.py").read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("failure_class_tags(", text)
        self.assertIn("context.instance.add_run_tags(context.run_id, tags)", text)
        self.assertIn("allow_retries=allow_step_retry(failure_class)", text)
        self.assertIn("service_running=self._service_running()", text)

    def test_daily_job_retry_budget_is_two(self):
        text = (PKG / "jobs.py").read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("TRANSIENT_RETRY_POLICY = dg.RetryPolicy(", text)
        self.assertIn("max_retries=2", text)
        self.assertIn("op_retry_policy=TRANSIENT_RETRY_POLICY", text)

    def test_r03_harness_runs_adapter_probe_and_sensor_transition(self):
        path = ROOT / "acceptance/phase3c/r03_infrastructure_recovery.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("execute_in_process(", text)
        self.assertIn("spark_exec_attempts", text)
        self.assertIn("instance.create_run_for_job", text)
        self.assertIn("instance.report_run_failed", text)
        self.assertIn('return_value=False', text)
        self.assertIn('return_value=True', text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("infrastructure_failure_after_runtime_recovered", text)


if __name__ == "__main__":
    unittest.main()
