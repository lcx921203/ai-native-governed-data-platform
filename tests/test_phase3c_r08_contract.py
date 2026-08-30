"""Static/pure contracts for R08 bounded replay-budget exhaustion."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "orchestration/dagster/commerce_dagster"
sys.path.insert(0, str(PKG))

from failure_classification import FailureClass  # noqa:E402
from recovery_policy import RecoveryAction, RecoveryObservation, decide_recovery  # noqa:E402


class R08PolicyContractTest(unittest.TestCase):
    def test_finished_failed_attempt_one_exhausts_budget(self) -> None:
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

    def test_budget_guard_precedes_replay_safe_failure_class(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_policy.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "decide_recovery"
        )
        rendered = ast.unparse(function)
        self.assertLess(
            rendered.index("if observation.auto_replay_attempts >= max_auto_replays"),
            rendered.index(
                "if observation.failure_class is FailureClass.TRANSIENT_RUNTIME"
            ),
        )

    def test_active_attempt_one_still_waits_before_budget_escalation(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=True,
                failed_run=True,
                failure_class=FailureClass.TRANSIENT_RUNTIME,
                infrastructure_healthy=True,
                auto_replay_attempts=1,
            )
        )
        self.assertIs(decision.action, RecoveryAction.WAIT)
        self.assertEqual(decision.reason_code, "active_run_owns_partition")


class R08SourceContractTest(unittest.TestCase):
    def test_budget_is_derived_from_persisted_recovery_runs(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_state.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("auto_replay_attempts = sum", text)
        self.assertIn("RECOVERY_TAG", text)
        self.assertIn("AUTO_RECOVERY_TAG_VALUE", text)

    def test_runtime_harness_persists_failed_attempt_one_and_forbids_attempt_two(self) -> None:
        path = ROOT / "acceptance/phase3c/r08_replay_budget_exhausted.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("DagsterInstance.local_temp", text)
        self.assertIn("instance.report_run_failed(run)", text)
        self.assertIn('RECOVERY_ATTEMPT_TAG: "1"', text)
        self.assertIn("auto_replay_budget_exhausted", text)
        self.assertIn("attempt_two_not_persisted", text)
        self.assertIn("attempt_two_run_key_not_persisted", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)

    def test_runtime_harness_keeps_runtime_evidence_boundary(self) -> None:
        path = ROOT / "acceptance/phase3c/r08_replay_budget_exhausted.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("does_not_prove", text)
        self.assertIn("real Dagster daemon launched attempt-1", text)
        self.assertIn("Docker/Spark", text)


if __name__ == "__main__":
    unittest.main()
