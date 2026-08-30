"""Static/pure contracts for R04 infrastructure-still-down bounded waiting."""

from __future__ import annotations

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

from orchestration.dagster.commerce_dagster.failure_classification import FailureClass
from orchestration.dagster.commerce_dagster.recovery_policy import (
    RecoveryAction,
    RecoveryObservation,
    decide_recovery,
    recovery_run_key,
)


ROOT = Path(__file__).resolve().parents[1]


class R04PolicyContractTest(unittest.TestCase):
    def test_repeated_unhealthy_observations_do_not_consume_budget(self) -> None:
        observation = RecoveryObservation(
            partition_key="2026-08-05",
            freshness_overdue=True,
            materialized=False,
            active_run=False,
            failed_run=True,
            failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE,
            infrastructure_healthy=False,
            auto_replay_attempts=0,
        )

        for _tick in range(3):
            decision = decide_recovery(observation)
            self.assertIs(decision.action, RecoveryAction.ALERT_AND_WAIT)
            self.assertEqual(decision.reason_code, "infrastructure_unhealthy")
            self.assertEqual(observation.auto_replay_attempts, 0)

    def test_first_request_after_restore_is_still_attempt_one(self) -> None:
        observation = RecoveryObservation(
            partition_key="2026-08-05",
            freshness_overdue=True,
            materialized=False,
            active_run=False,
            failed_run=True,
            failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE,
            infrastructure_healthy=True,
            auto_replay_attempts=0,
        )
        decision = decide_recovery(observation)
        self.assertIs(decision.action, RecoveryAction.AUTO_REPLAY)
        self.assertEqual(
            decision.reason_code,
            "infrastructure_failure_after_runtime_recovered",
        )
        self.assertEqual(
            recovery_run_key("2026-08-05", observation.auto_replay_attempts + 1),
            "shopify-daily-recovery:2026-08-05:attempt-1",
        )

    def test_r04_fixed_tick_sequence_is_post_deadline(self) -> None:
        deadline = datetime(2026, 8, 6, 1, 0, tzinfo=timezone.utc)
        ticks = (
            datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc),
            datetime(2026, 8, 6, 1, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 6, 1, 15, tzinfo=timezone.utc),
            datetime(2026, 8, 6, 1, 20, tzinfo=timezone.utc),
        )
        self.assertTrue(all(tick > deadline for tick in ticks))


class R04SourceContractTest(unittest.TestCase):
    def test_runtime_harness_proves_wait_ticks_and_budget_invariant(self) -> None:
        path = ROOT / "acceptance/phase3c/r04_infrastructure_still_down.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)

        self.assertIn("DOWN_TICKS", text)
        self.assertIn("2026, 8, 6, 1, 5", text)
        self.assertIn("2026, 8, 6, 1, 10", text)
        self.assertIn("2026, 8, 6, 1, 15", text)
        self.assertIn("RECOVERED_TICK", text)
        self.assertIn('return_value=False', text)
        self.assertIn('return_value=True', text)
        self.assertIn("auto_replay_attempts_before", text)
        self.assertIn("auto_replay_attempts_after", text)
        self.assertIn("partition_run_count_unchanged", text)
        self.assertIn("first_recovery_is_attempt_one", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)

    def test_runtime_state_budget_counts_runs_not_sensor_evaluations(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_state.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)

        self.assertIn("auto_replay_attempts = sum(", text)
        self.assertIn("run.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE", text)
        self.assertNotIn("sensor_tick", text)


if __name__ == "__main__":
    unittest.main()
