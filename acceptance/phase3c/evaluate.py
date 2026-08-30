#!/usr/bin/env python3
"""Evaluate the hand-authored Phase 3C acceptance matrix against the pure policy."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .scenarios import SCENARIOS, AcceptanceScenario
except ImportError:  # allow direct `python acceptance/phase3c/evaluate.py`
    from scenarios import SCENARIOS, AcceptanceScenario

from orchestration.dagster.commerce_dagster.recovery_policy import decide_recovery


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    passed: bool
    actual_action: str
    actual_reason_code: str
    expected_action: str
    expected_reason_code: str


def evaluate_scenario(scenario: AcceptanceScenario) -> ScenarioResult:
    decision = decide_recovery(scenario.observation)
    return ScenarioResult(
        scenario_id=scenario.scenario_id,
        passed=(
            decision.action is scenario.expected_action
            and decision.reason_code == scenario.expected_reason_code
        ),
        actual_action=decision.action.value,
        actual_reason_code=decision.reason_code,
        expected_action=scenario.expected_action.value,
        expected_reason_code=scenario.expected_reason_code,
    )


def evaluate_all() -> tuple[ScenarioResult, ...]:
    return tuple(evaluate_scenario(s) for s in SCENARIOS)


def main() -> int:
    results = evaluate_all()
    passed = sum(1 for result in results if result.passed)
    payload = {
        "scenario_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "result": "PASS" if passed == len(results) else "FAIL",
        "scenarios": [asdict(result) for result in results],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if payload["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
