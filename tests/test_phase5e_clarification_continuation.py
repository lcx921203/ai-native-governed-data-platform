from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent.clarification import (
    ContinuationStatus,
    GovernedClarificationContinuation,
)
from agent.response import GovernedResponseComposer, AnswerStatus, ClaimKind, render_deterministic
from agent.router import DeterministicToolRouter, GovernedPlanExecutor, ExecutionStatus
from agent.semantic_query import (
    GovernedSemanticQueryPlanner,
    SemanticQueryClarification,
    SemanticQueryPlan,
    SemanticQuerySpec,
    SemanticQueryStatus,
)


ROOT = Path(__file__).resolve().parents[1]
QUESTION = "2026-08-05 品牌为 Coca Colaa 的 gross_sales 是多少？"


def planner() -> GovernedSemanticQueryPlanner:
    return GovernedSemanticQueryPlanner(ROOT)


def coordinator() -> GovernedClarificationContinuation:
    return GovernedClarificationContinuation(ROOT)


def pending_plan() -> SemanticQueryPlan:
    return planner().plan(metric="gross_sales", question=QUESTION)


def test_phase5e_policy_freezes_query_context_and_disallows_raw_query_surface() -> None:
    policy = yaml.safe_load((ROOT / "agent/contracts/clarification_policy.yml").read_text())
    assert policy["version"] == 1
    assert policy["principles"]["original_query_context_immutable"] is True
    assert policy["principles"]["reparse_original_question_on_resume"] is False
    assert policy["principles"]["confirmation_must_select_stored_candidate"] is True
    assert policy["principles"]["fuzzy_candidate_requires_user_confirmation"] is True
    assert policy["principles"]["arbitrary_sql"] is False
    assert policy["principles"]["arbitrary_where_clause"] is False


def test_semantic_query_exposes_structured_resumable_clarification() -> None:
    plan = pending_plan()
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert plan.spec is None
    assert plan.continuation_spec is not None
    assert plan.clarification is not None
    assert plan.clarification.kind == "DIMENSION_VALUE_CONFIRMATION"
    assert plan.clarification.raw_value == "Coca Colaa"
    assert plan.clarification.candidates[0]["dimension"] == "item__brand"
    assert plan.clarification.candidates[0]["value"] == "Coca-Cola"
    assert plan.continuation_spec.metric_names == ("gross_sales",)
    assert plan.continuation_spec.start_time == "2026-08-05T00:00:00Z"


def test_prepare_creates_deterministic_immutable_continuation() -> None:
    item = coordinator()
    a = item.prepare(pending_plan())
    b = item.prepare(pending_plan())
    assert a.continuation_id == b.continuation_id
    assert a.integrity_checksum == b.integrity_checksum
    assert a.base_spec == b.base_spec
    assert a.candidates[0].id == "CAND01"
    assert a.candidates[0].value == "Coca-Cola"


def test_single_candidate_affirmation_resumes_without_replanning_original_question(monkeypatch: pytest.MonkeyPatch) -> None:
    item = coordinator()
    state = item.prepare(pending_plan())

    def forbidden(*args, **kwargs):
        raise AssertionError("resume must not reparse/replan the original natural-language question")

    monkeypatch.setattr(item.planner, "plan", forbidden)
    monkeypatch.setattr(item.planner, "plan_metrics", forbidden)
    result = item.resume(state, user_reply="对", execute=False)
    assert result.status is ContinuationStatus.READY
    assert result.plan is not None and result.plan.status is SemanticQueryStatus.READY
    assert result.selected_candidate is not None
    filt = result.plan.spec.filters[-1]
    assert filt.dimension == "item__brand"
    assert filt.value == "Coca-Cola"
    assert filt.source.startswith("user_confirmed:FUZZY_CANDIDATE")
    assert "{{ Dimension('item__brand') }} = 'Coca-Cola'" in result.plan.command_preview


def test_rejection_never_executes_query(monkeypatch: pytest.MonkeyPatch) -> None:
    item = coordinator()
    state = item.prepare(pending_plan())

    def forbidden(*args, **kwargs):
        raise AssertionError("rejected clarification must never execute MetricFlow")

    monkeypatch.setattr(item.executor, "execute", forbidden)
    result = item.resume(state, user_reply="不是", execute=True)
    assert result.status is ContinuationStatus.REJECTED
    assert result.plan is None
    assert result.query_result is None


def test_unknown_confirmation_never_executes_query(monkeypatch: pytest.MonkeyPatch) -> None:
    item = coordinator()
    state = item.prepare(pending_plan())

    def forbidden(*args, **kwargs):
        raise AssertionError("unknown clarification reply must never execute MetricFlow")

    monkeypatch.setattr(item.executor, "execute", forbidden)
    result = item.resume(state, user_reply="随便吧", execute=True)
    assert result.status is ContinuationStatus.CLARIFICATION_REQUIRED
    assert result.plan is None


def test_mutated_continuation_is_blocked_by_integrity_check() -> None:
    item = coordinator()
    state = item.prepare(pending_plan())
    mutated = replace(state, raw_value="Pepsi")
    result = item.resume(mutated, user_reply="对")
    assert result.status is ContinuationStatus.BLOCKED
    assert "checksum" in result.warnings[0]


def test_candidate_can_be_selected_by_ordinal() -> None:
    item = coordinator()
    base = SemanticQuerySpec(
        metric="gross_sales",
        metrics=("gross_sales",),
        start_time="2026-08-05T00:00:00Z",
        end_time="2026-08-05T23:59:59Z",
    )
    plan = SemanticQueryPlan(
        status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
        question="2026-08-05 只看 Shared 的 gross_sales",
        continuation_spec=base,
        clarification=SemanticQueryClarification(
            kind="DIMENSION_VALUE_CONFIRMATION",
            raw_value="Shared",
            dimension_hint=None,
            candidates=(
                {"dimension": "store__region", "value": "Shared", "score": 1.0, "mode": "CANONICAL_EXACT", "evidence": "RUNTIME_VERIFIED", "source_mode": "METRICFLOW_RUNTIME"},
                {"dimension": "item__brand", "value": "Shared", "score": 1.0, "mode": "CANONICAL_EXACT", "evidence": "RUNTIME_VERIFIED", "source_mode": "METRICFLOW_RUNTIME"},
            ),
            evidence="RUNTIME_VERIFIED",
            source_mode="AMBIGUOUS_EXACT",
            prompt="请确认 Shared 指的是地区还是品牌。",
        ),
    )
    state = item.prepare(plan)
    result = item.resume(state, user_reply="2")
    assert result.status is ContinuationStatus.READY
    assert result.selected_candidate.dimension == "item__brand"


def test_existing_resolved_filters_are_frozen_and_preserved_after_confirmation() -> None:
    question = "2026-08-05 美国 品牌为 Coca Colaa 的 gross_sales 是多少？"
    plan = planner().plan(metric="gross_sales", question=question)
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert plan.continuation_spec is not None
    assert [(f.dimension, f.value) for f in plan.continuation_spec.filters] == [("store__country", "US")]

    result = coordinator().resume(coordinator().prepare(plan), user_reply="确认")
    assert result.status is ContinuationStatus.READY
    assert result.plan is not None and result.plan.spec is not None
    assert [(f.dimension, f.value) for f in result.plan.spec.filters] == [
        ("store__country", "US"),
        ("item__brand", "Coca-Cola"),
    ]
    assert result.plan.spec.start_time == "2026-08-05T00:00:00Z"
    assert result.plan.spec.end_time == "2026-08-05T23:59:59Z"


def test_execute_after_confirmation_reaches_existing_runtime_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHASE5B_ALLOW_METRICFLOW_QUERY", raising=False)
    monkeypatch.delenv("PHASE5A_ALLOW_METRICFLOW_QUERY", raising=False)
    item = coordinator()
    result = item.resume(item.prepare(pending_plan()), user_reply="yes", execute=True)
    assert result.status is ContinuationStatus.DEFERRED
    assert result.query_result is not None
    assert result.query_result.status is SemanticQueryStatus.DEFERRED
    assert result.query_result.validation == "NOT_EXECUTED"


def test_router_response_uses_explicit_clarification_status_and_claim() -> None:
    plan = DeterministicToolRouter(ROOT).plan(QUESTION)
    execution = GovernedPlanExecutor(ROOT).execute(plan)
    assert execution.status is ExecutionStatus.CLARIFICATION_REQUIRED
    envelope = GovernedResponseComposer(ROOT).compose(execution)
    assert envelope.status is AnswerStatus.CLARIFICATION_REQUIRED
    clarification_claims = [c for c in envelope.claims if c.kind is ClaimKind.CLARIFICATION_REQUEST]
    assert clarification_claims
    assert "Coca-Cola" in clarification_claims[0].text
    draft = render_deterministic(envelope)
    assert "Coca-Cola" in draft.answer
    assert "确认" in draft.answer


def test_phase5e_is_orchestrator_state_not_an_llm_callable_tool() -> None:
    import json

    schemas = json.loads((ROOT / "agent/contracts/tool_schemas.json").read_text())
    names = {item["name"] for item in schemas["tools"]}
    assert "resume_clarification" not in names
    assert "confirm_dimension_value" not in names
