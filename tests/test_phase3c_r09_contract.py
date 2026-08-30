from __future__ import annotations

import ast
import unittest
from pathlib import Path

from orchestration.dagster.commerce_dagster.failure_classification import FailureClass
from orchestration.dagster.commerce_dagster.recovery_policy import (
    RecoveryAction,
    RecoveryObservation,
    decide_recovery,
)


ROOT = Path(__file__).resolve().parents[1]


class R09PolicyContractTest(unittest.TestCase):
    def test_success_without_complete_partition_is_manual(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=False,
                failed_run=False,
                successful_run=True,
                failure_class=FailureClass.NONE,
                infrastructure_healthy=True,
                auto_replay_attempts=0,
                missed_schedule_eligible=True,
            )
        )
        self.assertIs(decision.action, RecoveryAction.ALERT_MANUAL)
        self.assertEqual(
            decision.reason_code,
            "successful_run_without_complete_partition",
        )

    def test_complete_partition_wins_over_historical_run_state(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=True,
                active_run=False,
                failed_run=True,
                successful_run=True,
                failure_class=FailureClass.TRANSIENT_RUNTIME,
                infrastructure_healthy=True,
            )
        )
        self.assertIs(decision.action, RecoveryAction.NO_ACTION)
        self.assertEqual(decision.reason_code, "partition_already_materialized")

    def test_success_incomplete_guard_precedes_no_run_branch(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_policy.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "decide_recovery"
        )
        rendered = ast.unparse(function)
        self.assertLess(
            rendered.index("if observation.successful_run and (not observation.materialized)"),
            rendered.index("if not observation.failed_run"),
        )


class R09SourceContractTest(unittest.TestCase):
    def test_state_reader_uses_exact_consumer_mart_materializations(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_state_current.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("SHOPIFY_DAILY_MART_ASSET_KEYS", text)
        self.assertIn("asset_partitions=[partition_key]", text)
        self.assertIn("successful_runs", text)
        self.assertIn("materialized=not missing_marts", text)

    def test_runtime_harness_seeds_success_with_only_eight_of_nine_marts(self) -> None:
        path = ROOT / "acceptance/phase3c/r09_success_incomplete_partition.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("AssetMaterialization", text)
        self.assertIn('MISSING_MART = "fulfillment_events"', text)
        self.assertIn("execute_in_process", text)
        self.assertIn("eight_expected_marts_materialized_by_same_run", text)
        self.assertIn("successful_run_without_complete_partition", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("no_auto_recovery_run_persisted", text)

    def test_runtime_harness_keeps_data_evidence_boundary(self) -> None:
        path = ROOT / "acceptance/phase3c/r09_success_incomplete_partition.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("does_not_prove", text)
        self.assertIn("real dbt/Spark daily run", text)
        self.assertIn("Iceberg consumer-table row completeness", text)


if __name__ == "__main__":
    unittest.main()
