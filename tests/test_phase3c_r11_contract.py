"""Static/pure contracts for R11 freshness-budget guard."""

from __future__ import annotations

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

from orchestration.dagster.commerce_dagster.automation_policy import (
    overdue_partition_keys,
    partition_deadline_utc,
    scheduled_tick_utc_for_partition,
)
from orchestration.dagster.commerce_dagster.failure_classification import FailureClass
from orchestration.dagster.commerce_dagster.recovery_policy import (
    RecoveryAction,
    RecoveryObservation,
    decide_recovery,
)


ROOT = Path(__file__).resolve().parents[1]
PARTITION_KEY = "2026-08-05"
BEFORE_DEADLINE = datetime(2026, 8, 6, 0, 40, tzinfo=timezone.utc)
JUST_BEFORE_DEADLINE = datetime(2026, 8, 6, 0, 59, 59, tzinfo=timezone.utc)
AT_DEADLINE = datetime(2026, 8, 6, 1, 0, tzinfo=timezone.utc)


class R11PolicyContractTest(unittest.TestCase):
    def test_within_freshness_budget_waits_even_when_other_risk_signals_exist(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key=PARTITION_KEY,
                freshness_overdue=False,
                materialized=False,
                active_run=True,
                failed_run=True,
                failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE,
                infrastructure_healthy=False,
                auto_replay_attempts=1,
            )
        )
        self.assertIs(decision.action, RecoveryAction.WAIT)
        self.assertEqual(decision.reason_code, "within_freshness_budget")

    def test_freshness_guard_precedes_active_infra_budget_and_failure_rules(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_policy.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "decide_recovery"
        )
        rendered = ast.unparse(function)
        freshness_index = rendered.index("if not observation.freshness_overdue")
        self.assertLess(freshness_index, rendered.index("if observation.active_run"))
        self.assertLess(
            freshness_index,
            rendered.index("if not observation.infrastructure_healthy"),
        )
        self.assertLess(
            freshness_index,
            rendered.index("if observation.auto_replay_attempts >= max_auto_replays"),
        )
        self.assertLess(
            freshness_index,
            rendered.index(
                "if observation.failure_class is FailureClass.TRANSIENT_RUNTIME"
            ),
        )

    def test_daily_tick_and_deadline_form_a_45_minute_budget(self) -> None:
        tick = scheduled_tick_utc_for_partition(PARTITION_KEY)
        deadline = partition_deadline_utc(PARTITION_KEY)
        self.assertEqual(tick, datetime(2026, 8, 6, 0, 15, tzinfo=timezone.utc))
        self.assertEqual(deadline, AT_DEADLINE)
        self.assertEqual(int((deadline - tick).total_seconds() // 60), 45)

    def test_target_partition_enters_recovery_candidates_only_at_deadline(self) -> None:
        self.assertNotIn(PARTITION_KEY, overdue_partition_keys(BEFORE_DEADLINE))
        self.assertNotIn(
            PARTITION_KEY,
            overdue_partition_keys(JUST_BEFORE_DEADLINE),
        )
        self.assertIn(PARTITION_KEY, overdue_partition_keys(AT_DEADLINE))


class R11SourceContractTest(unittest.TestCase):
    def test_sensor_candidate_selection_happens_before_state_collection(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/sensors.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "shopify_daily_recovery_sensor"
        )
        rendered = ast.unparse(function)
        self.assertLess(
            rendered.index("candidate_keys = overdue_partition_keys(now_utc)"),
            rendered.index("collect_partition_recovery_state"),
        )

    def test_runtime_harness_proves_candidate_and_policy_gates_without_runtime_overclaim(self) -> None:
        path = ROOT / "acceptance/phase3c/r11_freshness_guard.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("DagsterInstance.local_temp", text)
        self.assertIn("BEFORE_DEADLINE", text)
        self.assertIn("overdue_partition_keys(BEFORE_DEADLINE)", text)
        self.assertIn("freshness_overdue=False", text)
        self.assertIn("within_freshness_budget", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("no_auto_recovery_run_persisted", text)
        self.assertIn("does_not_prove", text)
        self.assertIn("real Dagster daemon", text)
        self.assertIn("Dagster preview Freshness", text)


if __name__ == "__main__":
    unittest.main()
