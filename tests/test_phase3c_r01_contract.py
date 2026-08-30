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
    partition_key_for_schedule_tick,
    scheduled_tick_utc_for_partition,
)


class Phase3CR01ContractTest(unittest.TestCase):
    def test_historical_story_tick_targets_aug05(self):
        tick = datetime(2026, 8, 6, 0, 15, tzinfo=timezone.utc)
        self.assertEqual(partition_key_for_schedule_tick(tick), "2026-08-05")
        self.assertEqual(scheduled_tick_utc_for_partition("2026-08-05"), tick)

    def test_schedule_has_explicit_runtime_origin_tag(self):
        text = (PKG / "schedules.py").read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn('"commerce/automation": "daily-schedule"', text)
        self.assertIn("build_schedule_from_partitioned_job", text)

    def test_r01a_uses_real_schedule_evaluation(self):
        path = ROOT / "acceptance/phase3c/r01_schedule_definition.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("defs.get_schedule_def", text)
        self.assertIn("build_schedule_context", text)
        self.assertIn("evaluate_tick", text)

    def test_r01_requires_same_run_materializations(self):
        path = ROOT / "acceptance/phase3c/r01_normal_schedule.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("record.event_log_entry.run_id == run.run_id", text)
        self.assertIn("missing_same_run_marts", text)
        self.assertIn("all_marts_before_01_00_utc_deadline", text)


if __name__ == "__main__":
    unittest.main()
