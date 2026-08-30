from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.analysis_session import AnalysisSessionStatus, GovernedAnalysisSession, SessionDeltaKind
from agent.semantic_query import GovernedSemanticQueryPlanner, SemanticQueryPlan, SemanticQueryResult, SemanticQueryStatus
from agent.time_context import ComparisonMode, GovernedTimeComparator, TimeComparisonContext

ROOT = Path(__file__).resolve().parents[1]
FIRST = "2026-08-01 到 2026-08-05 按天看 gross_sales"


def initial_plan():
    return GovernedSemanticQueryPlanner(ROOT).plan(metric="gross_sales", question=FIRST)


def state():
    return GovernedAnalysisSession(ROOT).start(initial_plan())


def test_policy_locks_equal_windows_runtime_evidence_and_no_sql():
    policy = yaml.safe_load((ROOT / "agent/contracts/time_comparison_policy.yml").read_text())
    assert policy["principles"]["equal_length_previous_period_required"] is True
    assert policy["principles"]["derived_change_requires_both_windows_runtime_verified"] is True
    assert policy["principles"]["arbitrary_sql"] is False
    assert policy["runtime"]["allow_env"] == "PHASE5G_ALLOW_COMPARATIVE_QUERY"


def test_previous_five_days_derives_exact_equal_window_and_aggregate_comparison():
    comparator = GovernedTimeComparator(ROOT)
    plan = comparator.plan(
        initial_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD, requested_days=5),
        question="和前5天比呢？",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.current_spec.start_time == "2026-08-01T00:00:00Z"
    assert plan.current_spec.end_time == "2026-08-05T23:59:59Z"
    assert plan.current_spec.group_by == ()
    assert plan.comparison_spec.start_time == "2026-07-27T00:00:00Z"
    assert plan.comparison_spec.end_time == "2026-07-31T23:59:59Z"
    assert plan.comparison_spec.group_by == ()
    assert "temporal display grain" in plan.warnings[0]


def test_yoy_uses_same_calendar_window_previous_year():
    comparator = GovernedTimeComparator(ROOT)
    plan = comparator.plan(
        initial_plan().spec,
        context=TimeComparisonContext(ComparisonMode.YEAR_OVER_YEAR),
        question="同比呢？",
    )
    assert plan.status is SemanticQueryStatus.READY
    assert plan.comparison_spec.start_time == "2025-08-01T00:00:00Z"
    assert plan.comparison_spec.end_time == "2025-08-05T23:59:59Z"


def test_mismatched_previous_day_count_requires_clarification():
    plan = GovernedTimeComparator(ROOT).plan(
        initial_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD, requested_days=7),
        question="和前7天比呢？",
    )
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert "equal-length" in plan.warnings[0]


def test_non_time_group_by_is_not_silently_collapsed():
    grouped = GovernedSemanticQueryPlanner(ROOT).plan(
        metric="gross_sales",
        question="2026-08-01 到 2026-08-05 按地区看 gross_sales",
    )
    assert grouped.status is SemanticQueryStatus.READY
    plan = GovernedTimeComparator(ROOT).plan(
        grouped.spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="环比呢？",
    )
    assert plan.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    assert "store__region" in plan.warnings[0]


def test_session_sets_previous_period_context_without_replanning_original(monkeypatch: pytest.MonkeyPatch):
    manager = GovernedAnalysisSession(ROOT)
    s = manager.start(initial_plan())

    def forbidden(*args, **kwargs):
        raise AssertionError("comparison follow-up must not reparse original question")

    monkeypatch.setattr(manager.planner, "plan", forbidden)
    monkeypatch.setattr(manager.planner, "plan_metrics", forbidden)
    result = manager.apply_follow_up(s, question="和前5天比呢？")
    assert result.status is AnalysisSessionStatus.READY
    assert result.delta_kind is SessionDeltaKind.SET_COMPARISON
    assert result.state.comparison.mode is ComparisonMode.PREVIOUS_PERIOD
    assert result.state.comparison.requested_days == 5
    assert result.state.current_spec == s.current_spec
    assert result.comparison_plan.comparison_spec.start_time == "2026-07-27T00:00:00Z"


def test_change_followup_reuses_existing_comparison_context():
    manager = GovernedAnalysisSession(ROOT)
    compared = manager.apply_follow_up(manager.start(initial_plan()), question="同比呢？")
    change = manager.apply_follow_up(compared.state, question="增长了多少？")
    assert change.status is AnalysisSessionStatus.READY
    assert change.delta_kind is SessionDeltaKind.COMPUTE_COMPARISON
    assert change.state.comparison.mode is ComparisonMode.YEAR_OVER_YEAR
    assert change.comparison_plan.comparison_spec.start_time == "2025-08-01T00:00:00Z"


def test_change_without_comparison_context_requires_clarification_and_no_mutation():
    s = state()
    result = GovernedAnalysisSession(ROOT).apply_follow_up(s, question="增长了多少？")
    assert result.status is AnalysisSessionStatus.CLARIFICATION_REQUIRED
    assert result.state == s
    assert "No governed comparison context" in result.warnings[0]


def test_metric_and_filter_deltas_inherit_comparison_context():
    manager = GovernedAnalysisSession(ROOT)
    compared = manager.apply_follow_up(manager.start(initial_plan()), question="和前5天比呢？")
    filtered = manager.apply_follow_up(compared.state, question="那只看 West 呢？")
    metric_added = manager.apply_follow_up(filtered.state, question="那再加上 AOV")
    assert metric_added.state.comparison == compared.state.comparison
    assert metric_added.state.current_spec.metric_names == ("gross_sales", "average_order_value")
    assert [(f.dimension, f.value) for f in metric_added.state.current_spec.filters] == [("store__region", "West")]


def test_clear_comparison_preserves_semantic_query_state():
    manager = GovernedAnalysisSession(ROOT)
    compared = manager.apply_follow_up(manager.start(initial_plan()), question="同比呢？")
    cleared = manager.apply_follow_up(compared.state, question="取消对比")
    assert cleared.status is AnalysisSessionStatus.READY
    assert cleared.delta_kind is SessionDeltaKind.CLEAR_COMPARISON
    assert cleared.state.comparison is None
    assert cleared.state.current_spec == compared.state.current_spec


def test_comparison_runtime_gate_defers_before_semantic_query(monkeypatch: pytest.MonkeyPatch):
    comparator = GovernedTimeComparator(ROOT)
    plan = comparator.plan(
        initial_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="环比呢？",
    )
    monkeypatch.delenv("PHASE5G_ALLOW_COMPARATIVE_QUERY", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("semantic query must not run before Phase 5G gate")

    monkeypatch.setattr(comparator.executor, "execute", forbidden)
    result = comparator.execute(plan)
    assert result.status is SemanticQueryStatus.DEFERRED
    assert result.validation == "NOT_EXECUTED"


def test_runtime_verified_both_windows_produce_absolute_and_growth(monkeypatch: pytest.MonkeyPatch):
    comparator = GovernedTimeComparator(ROOT)
    plan = comparator.plan(
        initial_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="环比增长了多少？",
    )
    monkeypatch.setenv("PHASE5G_ALLOW_COMPARATIVE_QUERY", "true")
    calls = []

    def fake_execute(query_plan: SemanticQueryPlan):
        calls.append(query_plan.spec.start_time)
        value = "150" if query_plan.spec.start_time.startswith("2026-08-01") else "100"
        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=query_plan,
            rows=[{"gross_sales": value}],
            columns=["gross_sales"],
            validation="FAKE_RUNTIME_VERIFIED",
        )

    monkeypatch.setattr(comparator.executor, "execute", fake_execute)
    result = comparator.execute(plan)
    assert result.status is SemanticQueryStatus.COMPLETE
    assert calls == ["2026-08-01T00:00:00Z", "2026-07-27T00:00:00Z"]
    assert result.rows[0].absolute_change == "50"
    assert result.rows[0].growth_rate_percent == "50"
    assert result.evidence == "RUNTIME_VERIFIED"


def test_zero_comparison_value_returns_null_growth_not_infinity(monkeypatch: pytest.MonkeyPatch):
    comparator = GovernedTimeComparator(ROOT)
    plan = comparator.plan(
        initial_plan().spec,
        context=TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD),
        question="环比增长了多少？",
    )
    monkeypatch.setenv("PHASE5G_ALLOW_COMPARATIVE_QUERY", "true")
    counter = {"n": 0}

    def fake_execute(query_plan):
        counter["n"] += 1
        value = "10" if counter["n"] == 1 else "0"
        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=query_plan,
            rows=[{"gross_sales": value}],
            columns=["gross_sales"],
        )

    monkeypatch.setattr(comparator.executor, "execute", fake_execute)
    result = comparator.execute(plan)
    assert result.status is SemanticQueryStatus.COMPLETE
    assert result.rows[0].growth_rate_percent is None
    assert any("undefined" in warning for warning in result.warnings)


def test_session_state_roundtrip_preserves_comparison_and_checksum():
    manager = GovernedAnalysisSession(ROOT)
    compared = manager.apply_follow_up(manager.start(initial_plan()), question="同比呢？")
    restored = manager.from_dict(compared.state.to_dict())
    assert restored == compared.state
    next_result = manager.apply_follow_up(restored, question="增长了多少？")
    assert next_result.status is AnalysisSessionStatus.READY
