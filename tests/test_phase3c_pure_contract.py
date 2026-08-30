from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "orchestration/dagster/commerce_dagster"
sys.path.insert(0, str(PKG))

from automation_policy import (  # noqa:E402
    freshness_budget_minutes,
    latest_overdue_partition_key,
    missed_schedule_auto_replay_eligible,
    partition_deadline_utc,
)
from failure_classification import (  # noqa:E402
    DbtFailureObservation,
    FailureClass,
    classify_dbt_failure,
)
from recovery_policy import RecoveryAction, RecoveryObservation, decide_recovery  # noqa:E402


class Phase3CPureContractTest(unittest.TestCase):
    def test_timing(self):
        self.assertEqual(freshness_budget_minutes(), 45)
        self.assertEqual(
            partition_deadline_utc("2026-08-05"),
            datetime(2026, 8, 6, 1, 0, tzinfo=timezone.utc),
        )

    def test_dbt_contract_fail(self):
        result = classify_dbt_failure(
            DbtFailureObservation(
                command_name="build",
                command_succeeded=False,
                run_results={"results": [{"unique_id": "test.x", "status": "fail"}]},
            )
        )
        self.assertEqual(result.failure_class, FailureClass.DATA_CONTRACT)

    def test_dbt_parse_fail_is_deterministic(self):
        result = classify_dbt_failure(
            DbtFailureObservation("parse", False, None)
        )
        self.assertEqual(result.failure_class, FailureClass.DETERMINISTIC_CODE)

    def test_dbt_compile_fail_without_proof_stays_unknown(self):
        result = classify_dbt_failure(
            DbtFailureObservation("compile", False, None)
        )
        self.assertEqual(result.failure_class, FailureClass.UNKNOWN)

    def test_unknown_fails_closed(self):
        decision = decide_recovery(
            RecoveryObservation(
                "2026-08-05",
                True,
                False,
                False,
                True,
                failure_class=FailureClass.UNKNOWN,
            )
        )
        self.assertEqual(decision.action, RecoveryAction.ALERT_MANUAL)

    def test_recovered_infra_replays_once(self):
        decision = decide_recovery(
            RecoveryObservation(
                "2026-08-05",
                True,
                False,
                False,
                True,
                failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE,
                infrastructure_healthy=True,
            )
        )
        self.assertEqual(decision.action, RecoveryAction.AUTO_REPLAY)

    def test_latest_overdue_partition_is_only_no_run_auto_replay_candidate(self):
        now = datetime(2026, 8, 6, 1, 5, tzinfo=timezone.utc)
        self.assertEqual(latest_overdue_partition_key(now), "2026-08-05")
        self.assertTrue(missed_schedule_auto_replay_eligible("2026-08-05", now))
        self.assertFalse(missed_schedule_auto_replay_eligible("2026-08-04", now))

    def test_historical_no_run_fails_closed(self):
        decision = decide_recovery(
            RecoveryObservation(
                "2026-08-04",
                True,
                False,
                False,
                False,
                missed_schedule_eligible=False,
            )
        )
        self.assertEqual(decision.action, RecoveryAction.ALERT_MANUAL)
        self.assertEqual(
            decision.reason_code,
            "historical_no_run_requires_manual_backfill",
        )

    def test_recent_missed_schedule_is_replayed_once(self):
        decision = decide_recovery(
            RecoveryObservation(
                "2026-08-05",
                True,
                False,
                False,
                False,
                missed_schedule_eligible=True,
            )
        )
        self.assertEqual(decision.action, RecoveryAction.AUTO_REPLAY)
        self.assertEqual(decision.reason_code, "missed_schedule_or_no_run")


if __name__ == "__main__":
    unittest.main()
