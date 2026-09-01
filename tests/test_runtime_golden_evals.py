"""Synthetic Runtime Golden Eval 契约测试。"""

from pathlib import Path

import yaml

from agent.evals import (
    GovernedRuntimeGoldenEvalRunner,
    RuntimeGoldenStatus,
    render_runtime_golden_report,
)


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_golden_policy_keeps_synthetic_and_live_accuracy_separate():
    policy = yaml.safe_load(
        (ROOT / "agent/contracts/runtime_golden_eval_policy.yml").read_text(
            encoding="utf-8"
        )
    )
    assert policy["principles"]["synthetic_fixture_is_not_production_accuracy"] is True
    assert policy["principles"]["full_agent_runtime_path_is_exercised"] is True
    assert policy["principles"]["changed_semantics_make_golden_stale"] is True
    assert policy["principles"]["live_golden_requires_separate_approved_manifest"] is True


def test_runtime_golden_manifest_is_fingerprinted_and_has_four_cases():
    runner = GovernedRuntimeGoldenEvalRunner(ROOT)
    cases = runner.load_cases()

    assert len(cases) == 4
    assert not runner._stale_sources()
    assert len(runner.manifest["source_fingerprints"]) >= 3
    assert runner.manifest["fixture"]["git_blob_sha"]


def test_full_path_synthetic_runtime_golden_passes():
    runner = GovernedRuntimeGoldenEvalRunner(ROOT)
    report = runner.run()

    failed = [
        result for result in report.results
        if result.status is not RuntimeGoldenStatus.PASS
    ]
    assert not failed, [item.to_dict() for item in failed]
    assert report.pass_rate == 1.0
    runner.assert_gate(report)

    text = render_runtime_golden_report(report)
    assert "passed=4/4" in text
    assert "pass_rate=100.00%" in text
