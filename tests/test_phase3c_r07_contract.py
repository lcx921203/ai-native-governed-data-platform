"""Static/pure contracts for R07 duplicate recovery protection."""

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


class R07PolicyContractTest(unittest.TestCase):
    def test_active_owner_waits_even_when_attempt_one_is_already_persisted(self) -> None:
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

    def test_finished_failed_attempt_one_exhausts_budget_after_owner_disappears(self) -> None:
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


class R07SourceContractTest(unittest.TestCase):
    def test_state_reader_counts_persisted_auto_recovery_runs_as_budget(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_state.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("active_runs", text)
        self.assertIn("ACTIVE_RUN_STATUSES", text)
        self.assertIn("RECOVERY_TAG", text)
        self.assertIn("AUTO_RECOVERY_TAG_VALUE", text)
        self.assertIn("auto_replay_attempts = sum", text)

    def test_policy_checks_active_owner_before_budget(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_policy.py"
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "decide_recovery"
        )
        rendered = ast.unparse(function)
        self.assertLess(
            rendered.index("if observation.active_run"),
            rendered.index("if observation.auto_replay_attempts >= max_auto_replays"),
        )

    def test_runtime_harness_uses_persistent_state_and_real_sensor_definition(self) -> None:
        path = ROOT / "acceptance/phase3c/r07_duplicate_recovery_guard.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("DagsterInstance.local_temp", text)
        self.assertIn("instance.create_run_for_job", text)
        self.assertIn("RECOVERY_TAG: AUTO_RECOVERY_TAG_VALUE", text)
        self.assertIn('RECOVERY_ATTEMPT_TAG: "1"', text)
        self.assertIn("collect_partition_recovery_state", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("active_run_owns_partition", text)
        self.assertIn("auto_recovery_run_count_unchanged", text)
        self.assertIn("replay_budget_not_incremented_by_sensor_poll", text)

    def test_harness_does_not_claim_real_daemon_run_key_dedup(self) -> None:
        path = ROOT / "acceptance/phase3c/r07_duplicate_recovery_guard.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("does_not_prove", text)
        self.assertIn("run_key deduplication", text)


if __name__ == "__main__":
    unittest.main()
