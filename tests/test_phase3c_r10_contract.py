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


class R10PolicyContractTest(unittest.TestCase):
    def test_complete_partition_wins_over_historical_failure(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=True,
                active_run=False,
                failed_run=True,
                successful_run=False,
                failure_class=FailureClass.TRANSIENT_RUNTIME,
                infrastructure_healthy=True,
                auto_replay_attempts=0,
                missed_schedule_eligible=True,
            )
        )
        self.assertIs(decision.action, RecoveryAction.NO_ACTION)
        self.assertEqual(decision.reason_code, "partition_already_materialized")

    def test_complete_partition_even_precedes_infrastructure_and_budget(self) -> None:
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=True,
                active_run=False,
                failed_run=True,
                failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE,
                infrastructure_healthy=False,
                auto_replay_attempts=1,
            )
        )
        self.assertIs(decision.action, RecoveryAction.NO_ACTION)
        self.assertEqual(decision.reason_code, "partition_already_materialized")

    def test_materialized_guard_precedes_all_recovery_decision_branches(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_policy.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "decide_recovery"
        )
        rendered = ast.unparse(function)
        materialized_index = rendered.index("if observation.materialized")
        for later_branch in (
            "if not observation.freshness_overdue",
            "if observation.active_run",
            "if not observation.infrastructure_healthy",
            "if observation.auto_replay_attempts >= max_auto_replays",
            "if observation.successful_run and (not observation.materialized)",
            "if not observation.failed_run",
        ):
            self.assertLess(materialized_index, rendered.index(later_branch))


class R10SourceContractTest(unittest.TestCase):
    def test_state_reader_completeness_is_derived_from_all_consumer_marts(self) -> None:
        path = ROOT / "orchestration/dagster/commerce_dagster/recovery_state_current.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("SHOPIFY_DAILY_MART_ASSET_KEYS", text)
        self.assertIn("materialized=not missing_marts", text)
        self.assertIn("asset_partitions=[partition_key]", text)

    def test_runtime_harness_seeds_failure_then_all_nine_materializations(self) -> None:
        path = ROOT / "acceptance/phase3c/r10_partition_already_complete.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("report_run_failed", text)
        self.assertIn("AssetMaterialization", text)
        self.assertIn("SHOPIFY_DAILY_MART_ASSET_KEYS", text)
        self.assertIn("all_nine_marts_materialized_by_repair_run", text)
        self.assertIn("partition_already_materialized", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn("no_auto_recovery_run_persisted", text)

    def test_runtime_harness_keeps_orchestration_vs_data_boundary(self) -> None:
        path = ROOT / "acceptance/phase3c/r10_partition_already_complete.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("does_not_prove", text)
        self.assertIn("Iceberg row-level completeness", text)
        self.assertIn("manual backfill", text)


if __name__ == "__main__":
    unittest.main()
