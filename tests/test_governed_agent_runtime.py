"""Governed Single Agent Runtime 端到端编排契约测试。

测试目标不是连接真实 MetricFlow / Qdrant，而是确认：
- 所有请求只有一个主 Runtime；
- ANALYSIS 进入专用 Analysis Pipeline；
- 非 ANALYSIS 继续复用旧 ToolPlan Executor；
- Claim Ledger 在 Renderer 前；
- Answer Validator 最后强制执行。
"""

from pathlib import Path

from agent.analysis_planner import (
    AnalysisExecution,
    AnalysisExecutionStatus,
    AnalysisUnitExecution,
    AnalysisUnitExecutionStatus,
    AnalysisUnitKind,
)
from agent.response import (
    AnswerDraft,
    AnswerStatus,
    Claim,
    ClaimKind,
    ResponseEnvelope,
    render_deterministic,
)
from agent.router import ExecutionStatus, PlanExecution
from agent.runtime import AgentRuntimeStatus, GovernedAgentRuntime
from agent.validation import AnalysisValidationResult, ValidationDecision


ROOT = Path(__file__).resolve().parents[1]


class FakeStandardExecutor:
    """返回一个静态 Metric Definition Tool 结果，避免测试真实 Runtime。"""

    def execute(self, plan):
        if plan.status.value == "BLOCKED":
            return PlanExecution(plan, ExecutionStatus.BLOCKED, warnings=list(plan.warnings))

        result = {
            "tool": "get_metric_context",
            "status": "ANSWERED",
            "evidence": "STATIC_CONTRACT",
            "payload": {
                "id": "gross_sales",
                "name": "Gross Sales",
                "description": "受治理毛销售额。",
                "definition": {
                    "type": "simple",
                    "agg": "sum",
                    "expr": "gross_sales_amount",
                    "source_file": "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml",
                },
                "related_models": ["order_items"],
            },
            "sources": [
                {
                    "location": "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml"
                }
            ],
            "warnings": [],
        }
        return PlanExecution(plan, ExecutionStatus.COMPLETE, results=[result])


class FakeAnalysisExecutor:
    """把 Analysis Plan 转成全部 runtime-verified 的测试执行结果。"""

    def execute_with_validation(self, plan):
        results = []
        for unit in plan.units:
            if unit.kind is AnalysisUnitKind.TIME_COMPARISON:
                payload = {
                    "rows": [
                        {
                            "metric": plan.target_metric,
                            "current_value": "80",
                            "comparison_value": "100",
                            "absolute_change": "-20",
                            "growth_rate_percent": "-20",
                        }
                    ]
                }
                evidence = "RUNTIME_VERIFIED"
            elif unit.kind is AnalysisUnitKind.BREAKDOWN:
                payload = {
                    "rows": [
                        {
                            "dimension_value": "West",
                            "absolute_change": "-12",
                            "contribution_percent": "60",
                        }
                    ]
                }
                evidence = "RUNTIME_VERIFIED"
            else:
                payload = {
                    "strongest_drivers": [
                        {
                            "dimension_value": "West",
                            "absolute_change": "-12",
                        }
                    ],
                    "no_new_metric_math": True,
                    "causal_claim_allowed": False,
                }
                evidence = "DERIVED_VERIFIED"

            results.append(
                AnalysisUnitExecution(
                    unit_id=unit.unit_id,
                    kind=unit.kind,
                    required=unit.required,
                    status=AnalysisUnitExecutionStatus.COMPLETE,
                    evidence=evidence,
                    result=payload,
                    validation="TEST_VERIFIED",
                    attempt=1,
                )
            )

        validation = AnalysisValidationResult(
            decision=ValidationDecision.PASS,
            checked_units=len(results),
            evidence="RUNTIME_VERIFIED",
        )
        return AnalysisExecution(
            plan=plan,
            status=AnalysisExecutionStatus.COMPLETE,
            unit_results=tuple(results),
            validation_result=validation,
            retry_rounds=0,
        )


def test_blocked_sql_fails_before_context_loading_and_still_validates_answer():
    runtime = GovernedAgentRuntime(ROOT, plan_executor=FakeStandardExecutor())

    result = runtime.run("select * from orders")

    assert result.status is AgentRuntimeStatus.BLOCKED
    assert result.answer_validated is True
    stages = [item.stage for item in result.stage_trace]
    assert stages[0] == "router"
    assert "context_loader" not in stages
    assert stages[-1] == "answer_validator"


def test_metric_definition_runs_through_one_runtime_and_existing_executor():
    runtime = GovernedAgentRuntime(ROOT, plan_executor=FakeStandardExecutor())

    result = runtime.run("gross_sales 的定义是什么？")

    assert result.status is AgentRuntimeStatus.ANSWERED
    assert result.answer_validated is True
    assert result.context_bundle is not None
    assert "Gross Sales" in result.answer

    stages = [item.stage for item in result.stage_trace]
    assert stages == [
        "router",
        "context_planner",
        "context_loader",
        "executor",
        "claim_ledger",
        "renderer",
        "answer_validator",
    ]


def test_analysis_uses_skill_planner_executor_validation_claim_ledger():
    runtime = GovernedAgentRuntime(
        ROOT,
        plan_executor=FakeStandardExecutor(),
        analysis_executor=FakeAnalysisExecutor(),
    )

    result = runtime.run(
        "为什么 2026-08-01 到 2026-08-07 gross_sales 环比下降？"
    )

    assert result.status is AgentRuntimeStatus.ANSWERED
    assert result.answer_validated is True
    assert result.analysis_plan.skill_id == "sales_decline_analysis"
    assert result.analysis_execution.validation_result.decision is ValidationDecision.PASS
    assert "Verified comparison" in result.answer
    assert "Verified driver lens" in result.answer

    stages = [item.stage for item in result.stage_trace]
    assert stages == [
        "router",
        "context_planner",
        "context_loader",
        "analysis_planner",
        "analysis_executor",
        "analysis_validation",
        "claim_ledger",
        "renderer",
        "answer_validator",
    ]


def test_analysis_without_reference_window_returns_clarification_before_execution():
    runtime = GovernedAgentRuntime(
        ROOT,
        plan_executor=FakeStandardExecutor(),
        analysis_executor=FakeAnalysisExecutor(),
    )

    result = runtime.run(
        "为什么 2026-08-01 到 2026-08-07 gross_sales 下降？"
    )

    assert result.status is AgentRuntimeStatus.CLARIFICATION_REQUIRED
    assert result.analysis_execution is None
    assert result.answer_validated is True
    assert "环比" in result.answer or "同比" in result.answer


def test_answer_validator_failure_fails_closed():
    def invalid_renderer(envelope):
        return AnswerDraft(
            answer="我引用了一个不存在的 claim。",
            used_claim_ids=("C999",),
        )

    runtime = GovernedAgentRuntime(
        ROOT,
        plan_executor=FakeStandardExecutor(),
        renderer=invalid_renderer,
    )

    result = runtime.run("gross_sales 的定义是什么？")

    assert result.status is AgentRuntimeStatus.ERROR
    assert result.answer_validated is False
    assert result.draft is None
    assert any("Answer Validator" in warning for warning in result.warnings)


def test_deterministic_renderer_now_exposes_governed_knowledge_evidence():
    envelope = ResponseEnvelope(
        question="设计原因是什么？",
        intent="KNOWLEDGE_QUERY",
        status=AnswerStatus.ANSWERED,
        claims=[
            Claim(
                id="C01",
                kind=ClaimKind.KNOWLEDGE_EVIDENCE,
                text="Knowledge evidence from governed corpus.",
                evidence="RETRIEVED_KNOWLEDGE",
            )
        ],
    )

    draft = render_deterministic(envelope)

    assert "Knowledge evidence from governed corpus." in draft.answer
    assert draft.used_claim_ids == ("C01",)
