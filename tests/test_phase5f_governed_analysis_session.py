from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import pytest,yaml
from agent.analysis_session import GovernedAnalysisSession,AnalysisSessionStatus,SessionDeltaKind
from agent.semantic_query import GovernedSemanticQueryPlanner,SemanticQueryStatus

ROOT=Path(__file__).resolve().parents[1]
FIRST='2026-08-01 到 2026-08-05 按天看 gross_sales'

def manager(): return GovernedAnalysisSession(ROOT)
def initial_plan(): return GovernedSemanticQueryPlanner(ROOT).plan(metric='gross_sales',question=FIRST)
def state(): return manager().start(initial_plan())

def test_policy_is_structured_delta_not_free_chat_memory():
    p=yaml.safe_load((ROOT/'agent/contracts/analysis_session_policy.yml').read_text())
    assert p['principles']['session_state_is_structured_not_free_chat_memory'] is True
    assert p['principles']['reparse_original_question_on_follow_up'] is False
    assert p['principles']['arbitrary_sql'] is False
    assert p['limits']['max_metrics']==3 and p['limits']['max_filters']==2

def test_start_freezes_ready_semantic_query_state():
    p=initial_plan(); assert p.status is SemanticQueryStatus.READY
    s=manager().start(p)
    assert s.revision==1 and s.turn_count==1
    assert s.current_spec.metric_names==('gross_sales',)
    assert s.current_spec.group_by==('metric_time__day',)
    assert s.current_spec.start_time=='2026-08-01T00:00:00Z'

def test_followup_adds_west_filter_without_replanning_original(monkeypatch:pytest.MonkeyPatch):
    m=manager(); s=m.start(initial_plan())
    def forbidden(*a,**k): raise AssertionError('follow-up must not reparse original question')
    monkeypatch.setattr(m.planner,'plan',forbidden); monkeypatch.setattr(m.planner,'plan_metrics',forbidden)
    r=m.apply_follow_up(s,question='那只看 West 呢？')
    assert r.status is AnalysisSessionStatus.READY
    assert r.delta_kind is SessionDeltaKind.ADD_FILTER
    spec=r.state.current_spec
    assert spec.metric_names==('gross_sales',)
    assert spec.group_by==('metric_time__day',)
    assert spec.start_time=='2026-08-01T00:00:00Z'
    assert [(f.dimension,f.value) for f in spec.filters]==[('store__region','West')]

def test_followup_add_metric_preserves_date_grain_and_filter():
    m=manager(); first=m.apply_follow_up(m.start(initial_plan()),question='那只看 West 呢？')
    second=m.apply_follow_up(first.state,question='那再加上 AOV')
    assert second.status is AnalysisSessionStatus.READY
    assert second.delta_kind is SessionDeltaKind.ADD_METRIC
    spec=second.state.current_spec
    assert spec.metric_names==('gross_sales','average_order_value')
    assert spec.group_by==('metric_time__day',)
    assert [(f.dimension,f.value) for f in spec.filters]==[('store__region','West')]

def test_same_dimension_followup_replaces_filter_not_duplicates():
    m=manager(); a=m.apply_follow_up(m.start(initial_plan()),question='那只看 West 呢？')
    b=m.apply_follow_up(a.state,question='那看 South 呢？')
    assert b.delta_kind is SessionDeltaKind.REPLACE_FILTER
    assert [(f.dimension,f.value) for f in b.state.current_spec.filters]==[('store__region','South')]

def test_remove_filter_preserves_other_state():
    m=manager(); a=m.apply_follow_up(m.start(initial_plan()),question='那只看 West 呢？')
    b=m.apply_follow_up(a.state,question='去掉地区条件')
    assert b.delta_kind is SessionDeltaKind.REMOVE_FILTER
    assert b.state.current_spec.filters==()
    assert b.state.current_spec.metric_names==('gross_sales',)

def test_unknown_followup_does_not_mutate_state():
    s=state(); r=manager().apply_follow_up(s,question='换个角度看看')
    assert r.status is AnalysisSessionStatus.CLARIFICATION_REQUIRED
    assert r.state==s

def test_raw_sql_followup_is_blocked():
    s=state(); r=manager().apply_follow_up(s,question="where region='West'")
    assert r.status is AnalysisSessionStatus.BLOCKED
    assert r.state==s

def test_mutated_state_is_blocked_by_checksum():
    s=state(); mutated=replace(s,last_question='tampered')
    r=manager().apply_follow_up(mutated,question='那只看 West 呢？')
    assert r.status is AnalysisSessionStatus.BLOCKED
    assert 'checksum' in r.warnings[0]

def test_execute_hands_off_to_existing_runtime_gate(monkeypatch:pytest.MonkeyPatch):
    monkeypatch.setenv('PHASE5F_ALLOW_SESSION_EXECUTION','true')
    monkeypatch.delenv('PHASE5B_ALLOW_METRICFLOW_QUERY',raising=False)
    r=manager().apply_follow_up(state(),question='那只看 West 呢？',execute=True)
    assert r.status is AnalysisSessionStatus.DEFERRED
    assert r.query_result is not None and r.query_result.status is SemanticQueryStatus.DEFERRED

def test_session_mutation_is_not_public_llm_tool():
    schemas=json.loads((ROOT/'agent/contracts/tool_schemas.json').read_text())
    names={x['name'] for x in schemas['tools']}
    assert 'update_analysis_session' not in names
    assert 'apply_session_delta' not in names
    assert 'resume_analysis_session' not in names
