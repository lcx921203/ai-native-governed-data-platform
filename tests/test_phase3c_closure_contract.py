"""Phase 3C closure guards for cross-module invariants found during final audit."""

from __future__ import annotations

import ast
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "orchestration/dagster/commerce_dagster"
sys.path.insert(0, str(PKG))

from automation_policy import (  # noqa:E402
    SHOPIFY_DAILY_FRESHNESS_DEADLINE_CRON,
    SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES,
    SHOPIFY_DAILY_JOB_NAME,
    freshness_budget_minutes,
    partition_deadline_utc,
)
from failure_classification import FailureClass  # noqa:E402


class Phase3CClosureContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        ast.parse(text, filename=str(path))
        return text

    def test_time_contract_is_derived_from_one_deadline(self) -> None:
        policy = self.read(PKG / "automation_policy.py")
        self.assertIn("SHOPIFY_DAILY_FRESHNESS_DEADLINE_HOUR = 1", policy)
        self.assertIn("SHOPIFY_DAILY_FRESHNESS_DEADLINE_MINUTE = 0", policy)
        self.assertNotIn('SHOPIFY_DAILY_FRESHNESS_DEADLINE_CRON = "0 1 * * *"', policy)
        self.assertNotIn("SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES = 45", policy)
        self.assertEqual(SHOPIFY_DAILY_FRESHNESS_DEADLINE_CRON, "0 1 * * *")
        self.assertEqual(SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES, 45)
        self.assertEqual(freshness_budget_minutes(), 45)
        self.assertEqual(
            partition_deadline_utc("2026-08-05"),
            datetime(2026, 8, 6, 1, 0, tzinfo=timezone.utc),
        )

    def test_missed_schedule_is_a_no_run_state_not_failure_class(self) -> None:
        self.assertFalse(hasattr(FailureClass, "MISSED_SCHEDULE"))
        classifier = self.read(PKG / "failure_classification.py")
        policy = self.read(PKG / "recovery_policy.py")
        self.assertNotIn('MISSED_SCHEDULE = "missed_schedule"', classifier)
        self.assertNotIn("explicit_missed_schedule", policy)
        self.assertIn("missed_schedule_eligible", policy)

    def test_daily_job_name_uses_shared_contract_constant(self) -> None:
        jobs = self.read(PKG / "jobs.py")
        self.assertIn("from .automation_policy import SHOPIFY_DAILY_JOB_NAME", jobs)
        self.assertIn("name=SHOPIFY_DAILY_JOB_NAME", jobs)
        self.assertEqual(SHOPIFY_DAILY_JOB_NAME, "shopify_daily_partition_job")

    def test_schedule_is_explicitly_stopped_until_runtime_acceptance(self) -> None:
        schedules = self.read(PKG / "schedules.py")
        self.assertIn("default_status=dg.DefaultScheduleStatus.STOPPED", schedules)

    def test_recovery_state_does_not_truncate_replay_budget_history(self) -> None:
        state = self.read(PKG / "recovery_state.py")
        function = ast.parse(state)
        collect = next(
            node
            for node in function.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "collect_partition_recovery_state"
        )
        rendered = ast.unparse(collect)
        self.assertNotIn("limit=50", rendered)
        self.assertIn("order_by='id'", rendered)
        self.assertIn("ascending=False", rendered)

    def test_every_production_spark_submit_script_exists(self) -> None:
        missing: list[str] = []
        references: list[str] = []
        for path in PKG.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "spark_submit" or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    references.append(first.value)
                    if not (ROOT / first.value).is_file():
                        missing.append(first.value)
        self.assertTrue(references, "expected production spark_submit script references")
        self.assertEqual(missing, [])

    def test_asset_checks_pass_the_partition_effective_window(self) -> None:
        checks = self.read(PKG / "checks/lakehouse.py")
        self.assertEqual(checks.count("script_args=shopify_source_window_args(context)"), 2)
        self.assertIn("check_raw_observations.py", checks)
        self.assertIn("check_source_idempotency.py", checks)

    def test_raw_observation_check_uses_half_open_source_window(self) -> None:
        path = ROOT / "lakehouse/jobs/check_raw_observations.py"
        text = self.read(path)
        self.assertIn('"--window-start", required=True', text)
        self.assertIn('"--window-end", required=True', text)
        self.assertIn('F.col("order_updated_at") >=', text)
        self.assertIn('F.col("order_updated_at") <', text)
        self.assertIn("if count <= 0", text)

    def test_source_idempotency_check_uses_business_version_key_and_window(self) -> None:
        path = ROOT / "lakehouse/jobs/check_source_idempotency.py"
        text = self.read(path)
        self.assertIn('"--window-start", required=True', text)
        self.assertIn('"--window-end", required=True', text)
        self.assertIn('F.col("first_source_updated_at")', text)
        self.assertIn('F.col("last_source_updated_at")', text)
        self.assertIn('touched.groupBy(*business_keys, "record_hash")', text)
        self.assertIn('F.col("count") > 1', text)


if __name__ == "__main__":
    unittest.main()
