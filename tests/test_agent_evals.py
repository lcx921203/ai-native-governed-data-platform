"""Agent Eval Framework 契约测试。"""

from pathlib import Path

from agent.evals import (
    EvalCaseStatus,
    GovernedAgentEvalRunner,
    GovernedEvalSuiteLoader,
    render_text_report,
)


ROOT = Path(__file__).resolve().parents[1]


def test_eval_loader_has_unique_repository_owned_cases():
    cases = GovernedEvalSuiteLoader(ROOT).load()

    assert len(cases) >= 16
    assert len({case.case_id for case in cases}) == len(cases)
    assert all(case.source_path.startswith("evals/") for case in cases)


def test_static_agent_regression_suite_passes():
    runner = GovernedAgentEvalRunner(ROOT)
    report = runner.run()

    failed = [
        result
        for result in report.results
        if result.status is not EvalCaseStatus.PASS
    ]

    assert not failed, [
        {
            "case": result.case.case_id,
            "status": result.status.value,
            "failed_checks": [
                check.to_dict() for check in result.checks if not check.passed
            ],
            "warnings": result.warnings,
        }
        for result in failed
    ]
    assert report.pass_rate == 1.0
    assert not report.critical_failures
    runner.assert_gate(report)


def test_eval_report_exposes_category_accuracy():
    report = GovernedAgentEvalRunner(ROOT).run(
        ["semantic_queries", "adversarial"]
    )
    summary = report.category_summary()

    assert "semantic" in summary
    assert "guardrail" in summary
    assert summary["semantic"]["pass_rate"] == 1.0
    assert summary["guardrail"]["pass_rate"] == 1.0

    text = render_text_report(report)
    assert "Agent Eval Report" in text
    assert "pass_rate=100.00%" in text
