"""Analysis Executor + Validation 的静态集成测试。

不执行真实 MetricFlow；通过 Fake Comparator / Breakdown 验证：
- 受治理单元按依赖执行；
- Runtime Evidence 才能 PASS；
- ERROR 触发 bounded retry；
- DEFERRED 不会盲目 retry；
- optional signal 失败时允许 PARTIAL，但不能污染证据摘要。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.analysis_planner import (
    AnalysisExecutionStatus,
    AnalysisPlan,
    AnalysisPlanStatus,
    AnalysisUnit,
    AnalysisUnitExecutionStatus,
    AnalysisUnitKind,
    GovernedAnalysisExecutor,
)
from agent.validation import ValidationDecision
from agent.semantic_query import SemanticQueryStatus


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeRuntimeResult:
    status: SemanticQueryStatus
    evidence: str
    validation: str
    rows: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "validation": self.validation,
            "rows": list(self.rows),
            "warnings": list(self.warnings),
        }


class FakeComparator:
    def __init__(self, scripted: dict[str, list[FakeRuntimeResult]] | None = None):
        self.scripted = scripted or {}
        self.calls: dict[str, int] = {}

    def execute(self, compiled_plan):
        key = str(compiled_plan)
        index = self.calls.get(key, 0)
        self.calls[key] = index + 1
        queue = self.scripted.get(key)
        if queue:
            return queue[min(index, len(queue) - 1)]
        return FakeRuntimeResult(
            SemanticQueryStatus.COMPLETE,
            "RUNTIME_VERIFIED",
            "BOTH_WINDOWS_RUNTIME_VERIFIED",
            rows=[{"gross_sales": "100", "comparison_value": "120"}],
        )


class FakeBreakdown:
    def __init__(self, result: FakeRuntimeResult | None = None):
        self.result = result or FakeRuntimeResult(
            SemanticQueryStatus.COMPLETE,
            "RUNTIME_VERIFIED",
            "BREAKDOWN_RUNTIME_VERIFIED",
            rows=[
                {
                    "dimension": "store__region",
                    "dimension_value": "West",
                    "metric": "gross_sales",
                    "absolute_change": "-20",
                    "growth_rate_percent": "-16.67",
                }
            ],
        )

    def execute(self, compiled_plan):
        return self.result


def _plan(*, include_optional: bool = False) -> AnalysisPlan:
    units = [
        AnalysisUnit(
            unit_id="01_baseline",
            kind=AnalysisUnitKind.TIME_COMPARISON,
            skill_step_id="baseline_compare",
            required=True,
            authority="MetricFlow",
            compiled_plan="baseline",
        ),
        AnalysisUnit(
            unit_id="02_region",
            kind=AnalysisUnitKind.BREAKDOWN,
            skill_step_id="governed_breakdown",
            required=True,
            authority="MetricFlow",
            compiled_plan="region",
            depends_on=("01_baseline",),
        ),
    ]
    if include_optional:
        units.append(
            AnalysisUnit(
                unit_id="03_optional",
                kind=AnalysisUnitKind.TIME_COMPARISON,
                skill_step_id="reversal_signal_check",
                required=False,
                authority="MetricFlow",
                compiled_plan="optional",
                depends_on=("01_baseline",),
            )
        )
    units.append(
        AnalysisUnit(
            unit_id="99_summary",
            kind=AnalysisUnitKind.EVIDENCE_SUMMARY,
            skill_step_id="summarize_ranked_drivers",
            required=True,
            authority="VERIFIED_ANALYSIS_EVIDENCE",
            compiled_plan={
                "evidence_only": True,
                "no_new_metric_math": True,
                "no_causal_claim_without_evidence": True,
            },
            depends_on=tuple(unit.unit_id for unit in units),
        )
    )
    return AnalysisPlan(
        status=AnalysisPlanStatus.READY,
        question="为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？",
        target_metric="gross_sales",
        skill_id="sales_decline_analysis",
        units=tuple(units),
    )


def test_verified_analysis_execution_passes_validation():
    execution = GovernedAnalysisExecutor(
        ROOT,
        comparator=FakeComparator(),
        breakdown=FakeBreakdown(),
    ).execute_with_validation(_plan())

    assert execution.status is AnalysisExecutionStatus.COMPLETE
    assert execution.validation_result.decision is ValidationDecision.PASS
    assert execution.unit_result("99_summary").evidence == "DERIVED_VERIFIED"
    assert execution.unit_result("99_summary").result["causal_claim_allowed"] is False


def test_retryable_required_error_is_retried_and_then_passes():
    comparator = FakeComparator(
        {
            "baseline": [
                FakeRuntimeResult(SemanticQueryStatus.ERROR, "STATIC_CONTRACT", "TRANSIENT_ERROR"),
                FakeRuntimeResult(SemanticQueryStatus.COMPLETE, "RUNTIME_VERIFIED", "BOTH_WINDOWS_RUNTIME_VERIFIED"),
            ]
        }
    )
    execution = GovernedAnalysisExecutor(
        ROOT,
        comparator=comparator,
        breakdown=FakeBreakdown(),
    ).execute_with_validation(_plan())

    assert execution.validation_result.decision is ValidationDecision.PASS
    assert execution.retry_rounds == 1
    assert execution.unit_result("01_baseline").attempt == 2
    assert execution.unit_result("02_region").status is AnalysisUnitExecutionStatus.COMPLETE


def test_deferred_required_unit_is_blocked_without_blind_retry():
    comparator = FakeComparator(
        {
            "baseline": [
                FakeRuntimeResult(SemanticQueryStatus.DEFERRED, "STATIC_CONTRACT", "RUNTIME_GATE_DISABLED")
            ]
        }
    )
    execution = GovernedAnalysisExecutor(
        ROOT,
        comparator=comparator,
        breakdown=FakeBreakdown(),
    ).execute_with_validation(_plan())

    assert execution.validation_result.decision is ValidationDecision.BLOCKED
    assert execution.retry_rounds == 0
    assert comparator.calls["baseline"] == 1


def test_optional_failure_allows_partial_and_summary_uses_only_verified_evidence():
    comparator = FakeComparator(
        {
            "optional": [
                FakeRuntimeResult(SemanticQueryStatus.ERROR, "STATIC_CONTRACT", "OPTIONAL_QUERY_FAILED")
            ]
        }
    )
    execution = GovernedAnalysisExecutor(
        ROOT,
        comparator=comparator,
        breakdown=FakeBreakdown(),
    ).execute_with_validation(_plan(include_optional=True))

    assert execution.status is AnalysisExecutionStatus.PARTIAL
    assert execution.validation_result.decision is ValidationDecision.PASS
    summary = execution.unit_result("99_summary").result
    evidence_ids = {item["unit_id"] for item in summary["evidence_units"]}
    assert "03_optional" not in evidence_ids


def test_complete_without_runtime_evidence_is_blocked():
    comparator = FakeComparator(
        {
            "baseline": [
                FakeRuntimeResult(SemanticQueryStatus.COMPLETE, "STATIC_CONTRACT", "QUERY_NOT_RUNTIME_VERIFIED")
            ]
        }
    )
    execution = GovernedAnalysisExecutor(
        ROOT,
        comparator=comparator,
        breakdown=FakeBreakdown(),
    ).execute_with_validation(_plan())

    assert execution.validation_result.decision is ValidationDecision.BLOCKED
    assert any(
        issue.code == "RUNTIME_EVIDENCE_REQUIRED"
        for issue in execution.validation_result.issues
    )
