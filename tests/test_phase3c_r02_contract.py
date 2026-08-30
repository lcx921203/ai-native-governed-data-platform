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
    missed_schedule_auto_replay_eligible,
)
from recovery_policy import (  # noqa:E402
    RecoveryAction,
    RecoveryObservation,
    decide_recovery,
    recovery_run_key,
)


class Phase3CR02ContractTest(unittest.TestCase):
    def test_fixed_story_recent_partition_is_eligible(self):
        now = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
        self.assertTrue(missed_schedule_auto_replay_eligible("2026-08-05", now))
        self.assertFalse(missed_schedule_auto_replay_eligible("2026-08-04", now))

    def test_missed_schedule_decision_is_bounded_auto_replay(self):
        decision = decide_recovery(
            RecoveryObservation(
                partition_key="2026-08-05",
                freshness_overdue=True,
                materialized=False,
                active_run=False,
                failed_run=False,
                infrastructure_healthy=True,
                auto_replay_attempts=0,
                missed_schedule_eligible=True,
            )
        )
        self.assertEqual(decision.action, RecoveryAction.AUTO_REPLAY)
        self.assertEqual(decision.reason_code, "missed_schedule_or_no_run")
        self.assertEqual(
            recovery_run_key("2026-08-05", 1),
            "shopify-daily-recovery:2026-08-05:attempt-1",
        )

    def test_sensor_uses_injectable_clock_and_explicit_recovery_tags(self):
        text = (PKG / "sensors.py").read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("def utc_now()", text)
        self.assertIn("missed_schedule_auto_replay_eligible", text)
        self.assertIn('"commerce/automation": "recovery-sensor"', text)
        self.assertIn("RECOVERY_REASON_TAG: decision.reason_code", text)

    def test_runtime_harness_invokes_real_sensor_with_persistent_instance(self):
        path = ROOT / "acceptance/phase3c/r02_missed_schedule.py"
        text = path.read_text(encoding="utf-8")
        ast.parse(text)
        self.assertIn("DagsterInstance.local_temp", text)
        self.assertIn("build_sensor_context", text)
        self.assertIn("shopify_daily_recovery_sensor(context)", text)
        self.assertIn('patch.object(sensor_module, "utc_now"', text)
        self.assertIn('"docker_compose_services_running"', text)
        self.assertIn('patch.object(', text)


if __name__ == "__main__":
    unittest.main()
