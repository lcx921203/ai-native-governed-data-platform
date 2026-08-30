"""冻结语义查询状态之上的受治理多轮 Analysis Session。

Session 只允许有限 delta：增加/删除过滤、设置比较窗口、breakdown 等；每次变更都有 turn、fingerprint 与 checksum。
工程边界：follow-up 不能打开 raw SQL，也不能绕过 Planner/MetricFlow 权威。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agent.analysis_session.contracts import (
    AnalysisSessionResult,
    AnalysisSessionState,
    AnalysisSessionStatus,
    SessionDeltaKind,
    SessionTurn,
)
from agent.breakdown_analysis import BreakdownAnalysisMode, GovernedComparativeBreakdown
from agent.dimension_resolution import DimensionResolutionStatus, GovernedDimensionValueResolver
from agent.router.deterministic import METRIC_ALIASES
from agent.semantic_query import (
    GovernedSemanticQueryPlanner,
    MetricFlowSemanticQueryExecutor,
    SemanticDimensionFilter,
    SemanticFilterOperator,
    SemanticQueryPlan,
    SemanticQuerySpec,
    SemanticQueryStatus,
)
from agent.time_context import ComparisonMode, GovernedTimeComparator, TimeComparisonContext


class GovernedAnalysisSession:
    """维护一个受治理分析会话的不可变查询上下文与 turn history。
    
    输入是已验证的初始计划和有限 follow-up；输出 AnalysisSessionResult，并记录每次允许/拒绝的状态变化。
    """
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/analysis_session_policy.yml").read_text(encoding="utf-8")
        )
        self.semantic_policy = yaml.safe_load(
            (self.root / "agent/contracts/semantic_query_policy.yml").read_text(encoding="utf-8")
        )
        self.time_policy = yaml.safe_load(
            (self.root / "agent/contracts/time_comparison_policy.yml").read_text(encoding="utf-8")
        )
        self.planner = GovernedSemanticQueryPlanner(self.root)
        self.resolver = GovernedDimensionValueResolver(self.root)
        self.executor = MetricFlowSemanticQueryExecutor(self.root)
        self.comparator = GovernedTimeComparator(self.root)
        self.breakdown = GovernedComparativeBreakdown(self.root)

    def start(self, plan: SemanticQueryPlan) -> AnalysisSessionState:
        """用初始 SemanticQueryPlan 创建会话状态并计算首个 fingerprint；不在这里执行数值查询。"""
        if plan.status is not SemanticQueryStatus.READY or plan.spec is None:
            raise ValueError("Analysis session requires a READY semantic query plan")
        seed = {
            "original_question": plan.question,
            "spec": plan.spec.to_dict(),
            "contract_version": int(self.policy["version"]),
        }
        sid = "sess_" + self._checksum(seed)[:16]
        turn = SessionTurn(
            revision=1,
            question=plan.question,
            delta_kind=SessionDeltaKind.NOOP,
            summary="Initial governed semantic query state",
        )
        return self._state(
            session_id=sid,
            original_question=plan.question,
            spec=plan.spec,
            revision=1,
            turn_count=1,
            last_question=plan.question,
            history=(turn,),
            comparison=None,
        )

    def apply_follow_up(
        self,
        state: AnalysisSessionState,
        *,
        question: str,
        execute: bool = False,
    ) -> AnalysisSessionResult:
        """解释一条 follow-up 为有限 session delta，并通过既有 Planner/Comparator/Breakdown 逻辑执行或拒绝。"""
        error = self._validate(state)
        if error:
            return AnalysisSessionResult(AnalysisSessionStatus.BLOCKED, state, question, warnings=(error,))
        if state.turn_count >= int(self.policy["limits"]["max_turns"]):
            return AnalysisSessionResult(
                AnalysisSessionStatus.BLOCKED,
                state,
                question,
                warnings=("Analysis session turn budget is exhausted.",),
            )
        text = question.strip()
        if not text:
            return AnalysisSessionResult(
                AnalysisSessionStatus.CLARIFICATION_REQUIRED,
                state,
                question,
                warnings=("A non-empty follow-up is required.",),
            )
        if self._contains_raw_query_surface(text):
            return AnalysisSessionResult(
                AnalysisSessionStatus.BLOCKED,
                state,
                question,
                warnings=("Raw SQL / raw where syntax is not allowed in governed analysis-session deltas.",),
            )

        # Comparison/session-control deltas are checked before generic filter/metric deltas.
        if self._is_clear_comparison(text):
            if state.comparison is None:
                return AnalysisSessionResult(
                    AnalysisSessionStatus.CLARIFICATION_REQUIRED,
                    state,
                    question,
                    warnings=("No governed comparison context exists to clear.",),
                )
            return self._commit_simple_delta(
                state,
                text,
                SessionDeltaKind.CLEAR_COMPARISON,
                state.current_spec,
                comparison=None,
                summary="Cleared governed comparison context while preserving semantic-query state.",
            )

        comparison_context = self._comparison_context(text)
        if comparison_context is not None:
            return self._set_comparison(state, text, comparison_context, execute=execute)

        # Once a comparison exists, ranking/contribution requests route to Phase 5H.
        breakdown_mode = self.breakdown.infer_mode(text)
        if state.comparison is not None and breakdown_mode is not BreakdownAnalysisMode.COMPARE:
            if breakdown_mode is BreakdownAnalysisMode.CONTRIBUTION:
                kind = SessionDeltaKind.CONTRIBUTION_ANALYSIS
            else:
                kind = SessionDeltaKind.RANK_BREAKDOWN
            return self._breakdown_follow_up(state, text, kind, breakdown_mode, execute=execute)

        if self._is_change_request(text):
            if state.comparison is None:
                return AnalysisSessionResult(
                    AnalysisSessionStatus.CLARIFICATION_REQUIRED,
                    state,
                    question,
                    warnings=("No governed comparison context exists; request previous-period or year-over-year comparison first.",),
                )
            return self._comparison_compute(state, text, execute=execute)

        delta = self._metric_delta(state.current_spec, text)
        if delta is None:
            delta = self._remove_filter_delta(state.current_spec, text)
        if delta is None:
            delta = self._filter_delta(state.current_spec, text)
        if delta is None:
            return AnalysisSessionResult(
                AnalysisSessionStatus.CLARIFICATION_REQUIRED,
                state,
                question,
                warnings=("Follow-up did not resolve to one governed metric/filter delta. State was not changed.",),
            )

        kind, spec, summary = delta
        if len(spec.metric_names) > int(self.policy["limits"]["max_metrics"]):
            return AnalysisSessionResult(
                AnalysisSessionStatus.BLOCKED,
                state,
                question,
                warnings=("Follow-up would exceed the governed metric-set limit.",),
            )
        if len(spec.filters) > int(self.policy["limits"]["max_filters"]):
            return AnalysisSessionResult(
                AnalysisSessionStatus.BLOCKED,
                state,
                question,
                warnings=("Follow-up would exceed the governed filter limit.",),
            )

        revision = state.revision + 1
        turn = SessionTurn(revision=revision, question=text, delta_kind=kind, summary=summary)
        new_state = self._state(
            session_id=state.session_id,
            original_question=state.original_question,
            spec=spec,
            revision=revision,
            turn_count=state.turn_count + 1,
            last_question=text,
            history=tuple([*state.history, turn]),
            comparison=state.comparison,
        )
        plan = SemanticQueryPlan(
            status=SemanticQueryStatus.READY,
            question=text,
            spec=spec,
            command_preview=self.planner.command_args(spec),
            warnings=[f"Analysis session {state.session_id} revision {revision}: {summary}"],
        )
        if not execute:
            return AnalysisSessionResult(AnalysisSessionStatus.READY, new_state, text, kind, plan=plan)
        if not self._session_runtime_enabled():
            return AnalysisSessionResult(
                AnalysisSessionStatus.DEFERRED,
                new_state,
                text,
                kind,
                plan=plan,
                warnings=(self._session_gate_warning(),),
            )
        result = self.executor.execute(plan)
        return AnalysisSessionResult(
            self._map_status(result.status),
            new_state,
            text,
            kind,
            plan=plan,
            query_result=result,
            warnings=tuple(result.warnings),
        )

    def _set_comparison(
        self,
        state: AnalysisSessionState,
        text: str,
        context: TimeComparisonContext,
        *,
        execute: bool,
    ) -> AnalysisSessionResult:
        """把明确的 comparison 意图写入会话状态，不改变原 Metric / 基础过滤权威。"""
        non_time = tuple(g for g in state.current_spec.group_by if not g.startswith("metric_time__"))
        if len(non_time) == 1:
            plan = self.breakdown.plan(
                state.current_spec,
                context=context,
                question=text,
                mode=BreakdownAnalysisMode.COMPARE,
            )
            if plan.status is not SemanticQueryStatus.READY:
                return AnalysisSessionResult(
                    self._map_status(plan.status), state, text, warnings=tuple(plan.warnings), breakdown_plan=plan
                )
            new_state = self._state_with_turn(
                state,
                text,
                SessionDeltaKind.SET_COMPARISON,
                f"Set {context.mode.value.lower()} comparison context.",
                comparison=context,
            )
            if not execute:
                return AnalysisSessionResult(
                    AnalysisSessionStatus.READY,
                    new_state,
                    text,
                    SessionDeltaKind.SET_COMPARISON,
                    breakdown_plan=plan,
                )
            if not self._session_runtime_enabled():
                return AnalysisSessionResult(
                    AnalysisSessionStatus.DEFERRED,
                    new_state,
                    text,
                    SessionDeltaKind.SET_COMPARISON,
                    breakdown_plan=plan,
                    warnings=(self._session_gate_warning(),),
                )
            result = self.breakdown.execute(plan)
            return AnalysisSessionResult(
                self._map_status(result.status),
                new_state,
                text,
                SessionDeltaKind.SET_COMPARISON,
                breakdown_plan=plan,
                breakdown_result=result,
                warnings=tuple(result.warnings),
            )

        comparison_plan = self.comparator.plan(state.current_spec, context=context, question=text)
        if comparison_plan.status is not SemanticQueryStatus.READY:
            return AnalysisSessionResult(
                self._map_status(comparison_plan.status),
                state,
                text,
                comparison_plan=comparison_plan,
                warnings=tuple(comparison_plan.warnings),
            )
        new_state = self._state_with_turn(
            state,
            text,
            SessionDeltaKind.SET_COMPARISON,
            f"Set {context.mode.value.lower()} comparison context.",
            comparison=context,
        )
        if not execute:
            return AnalysisSessionResult(
                AnalysisSessionStatus.READY,
                new_state,
                text,
                SessionDeltaKind.SET_COMPARISON,
                comparison_plan=comparison_plan,
            )
        if not self._session_runtime_enabled():
            return AnalysisSessionResult(
                AnalysisSessionStatus.DEFERRED,
                new_state,
                text,
                SessionDeltaKind.SET_COMPARISON,
                comparison_plan=comparison_plan,
                warnings=(self._session_gate_warning(),),
            )
        result = self.comparator.execute(comparison_plan)
        return AnalysisSessionResult(
            self._map_status(result.status),
            new_state,
            text,
            SessionDeltaKind.SET_COMPARISON,
            comparison_plan=comparison_plan,
            comparison_result=result,
            warnings=tuple(result.warnings),
        )

    def _comparison_compute(self, state: AnalysisSessionState, text: str, *, execute: bool) -> AnalysisSessionResult:
        """基于会话中冻结的 semantic spec 构建并执行受治理时间比较。"""
        assert state.comparison is not None
        non_time = tuple(g for g in state.current_spec.group_by if not g.startswith("metric_time__"))
        if len(non_time) == 1:
            plan = self.breakdown.plan(
                state.current_spec,
                context=state.comparison,
                question=text,
                mode=BreakdownAnalysisMode.COMPARE,
            )
            if plan.status is not SemanticQueryStatus.READY:
                return AnalysisSessionResult(self._map_status(plan.status), state, text, warnings=tuple(plan.warnings), breakdown_plan=plan)
            return self._commit_breakdown(state, text, SessionDeltaKind.COMPUTE_COMPARISON, plan, execute=execute)

        plan = self.comparator.plan(state.current_spec, context=state.comparison, question=text)
        if plan.status is not SemanticQueryStatus.READY:
            return AnalysisSessionResult(self._map_status(plan.status), state, text, comparison_plan=plan, warnings=tuple(plan.warnings))
        new_state = self._state_with_turn(
            state,
            text,
            SessionDeltaKind.COMPUTE_COMPARISON,
            f"Reused governed {state.comparison.mode.value} comparison context to compute change.",
            comparison=state.comparison,
        )
        if not execute:
            return AnalysisSessionResult(
                AnalysisSessionStatus.READY,
                new_state,
                text,
                SessionDeltaKind.COMPUTE_COMPARISON,
                comparison_plan=plan,
            )
        if not self._session_runtime_enabled():
            return AnalysisSessionResult(
                AnalysisSessionStatus.DEFERRED,
                new_state,
                text,
                SessionDeltaKind.COMPUTE_COMPARISON,
                comparison_plan=plan,
                warnings=(self._session_gate_warning(),),
            )
        result = self.comparator.execute(plan)
        return AnalysisSessionResult(
            self._map_status(result.status),
            new_state,
            text,
            SessionDeltaKind.COMPUTE_COMPARISON,
            comparison_plan=plan,
            comparison_result=result,
            warnings=tuple(result.warnings),
        )

    def _breakdown_follow_up(
        self,
        state: AnalysisSessionState,
        text: str,
        kind: SessionDeltaKind,
        mode: BreakdownAnalysisMode,
        *,
        execute: bool,
    ) -> AnalysisSessionResult:
        """识别 breakdown follow-up，要求明确的受治理维度后再进入 Comparative Breakdown。"""
        assert state.comparison is not None
        plan = self.breakdown.plan(state.current_spec, context=state.comparison, question=text, mode=mode)
        if plan.status is not SemanticQueryStatus.READY:
            return AnalysisSessionResult(
                self._map_status(plan.status),
                state,
                text,
                breakdown_plan=plan,
                warnings=tuple(plan.warnings),
            )
        return self._commit_breakdown(state, text, kind, plan, execute=execute)

    def _commit_breakdown(self, state, text, kind, plan, *, execute):
        """把 breakdown 结果与 turn 元数据提交到新会话状态，保留前一状态可追溯性。"""
        summary = {
            SessionDeltaKind.RANK_BREAKDOWN: f"Prepared governed {plan.mode.value} ranking over existing comparison context.",
            SessionDeltaKind.CONTRIBUTION_ANALYSIS: "Prepared governed contribution analysis over existing comparison context.",
            SessionDeltaKind.COMPUTE_COMPARISON: "Reused governed comparison context for grouped change analysis.",
        }.get(kind, "Prepared governed comparative breakdown.")
        new_state = self._state_with_turn(state, text, kind, summary, comparison=state.comparison)
        if not execute:
            return AnalysisSessionResult(AnalysisSessionStatus.READY, new_state, text, kind, breakdown_plan=plan)
        if not self._session_runtime_enabled():
            return AnalysisSessionResult(
                AnalysisSessionStatus.DEFERRED,
                new_state,
                text,
                kind,
                breakdown_plan=plan,
                warnings=(self._session_gate_warning(),),
            )
        result = self.breakdown.execute(plan)
        return AnalysisSessionResult(
            self._map_status(result.status),
            new_state,
            text,
            kind,
            breakdown_plan=plan,
            breakdown_result=result,
            warnings=tuple(result.warnings),
        )

    def _commit_simple_delta(self, state, text, kind, spec, *, comparison, summary):
        """提交普通 filter/comparison delta，并重新计算 fingerprint/checksum。"""
        new_state = self._state_with_turn(state, text, kind, summary, spec=spec, comparison=comparison)
        return AnalysisSessionResult(AnalysisSessionStatus.READY, new_state, text, kind)

    def _state_with_turn(
        self,
        state: AnalysisSessionState,
        text: str,
        kind: SessionDeltaKind,
        summary: str,
        *,
        spec: SemanticQuerySpec | None = None,
        comparison: TimeComparisonContext | None,
    ) -> AnalysisSessionState:
        """把一个新 turn 追加到会话历史并返回新的不可变状态对象。"""
        revision = state.revision + 1
        turn = SessionTurn(revision=revision, question=text, delta_kind=kind, summary=summary)
        return self._state(
            session_id=state.session_id,
            original_question=state.original_question,
            spec=spec or state.current_spec,
            revision=revision,
            turn_count=state.turn_count + 1,
            last_question=text,
            history=tuple([*state.history, turn]),
            comparison=comparison,
        )

    def from_dict(self, payload: dict[str, Any]) -> AnalysisSessionState:
        """从序列化状态恢复 Analysis Session，并重新做完整性验证。"""
        s = payload["current_spec"]
        filters = tuple(
            SemanticDimensionFilter(
                f["dimension"],
                SemanticFilterOperator(f["operator"]),
                f["value"],
                f.get("source", "session_state"),
            )
            for f in s.get("filters", [])
        )
        spec = SemanticQuerySpec(
            metric=s["metric"],
            metrics=tuple(s.get("metrics") or [s["metric"]]),
            start_time=s["start_time"],
            end_time=s["end_time"],
            group_by=tuple(s.get("group_by", [])),
            filters=filters,
            limit=int(s.get("limit", 20)),
        )
        history = tuple(
            SessionTurn(
                revision=int(x["revision"]),
                question=x["question"],
                delta_kind=SessionDeltaKind(x["delta_kind"]),
                summary=x["summary"],
            )
            for x in payload.get("history", [])
        )
        comparison_payload = payload.get("comparison")
        comparison = None
        if comparison_payload:
            comparison = TimeComparisonContext(
                mode=ComparisonMode(comparison_payload["mode"]),
                requested_days=comparison_payload.get("requested_days"),
                label=comparison_payload.get("label", ""),
            )
        return AnalysisSessionState(
            session_id=payload["session_id"],
            contract_version=int(payload["contract_version"]),
            original_question=payload["original_question"],
            current_spec=spec,
            revision=int(payload["revision"]),
            turn_count=int(payload["turn_count"]),
            last_question=payload["last_question"],
            history=history,
            integrity_checksum=payload["integrity_checksum"],
            comparison=comparison,
        )

    def _comparison_context(self, text: str) -> TimeComparisonContext | None:
        """从会话当前状态提取受治理比较上下文，供 Comparator 复用。"""
        low = text.lower()
        markers = self.time_policy.get("markers", {})
        if any(marker.lower() in low for marker in markers.get("year_over_year", [])):
            return TimeComparisonContext(ComparisonMode.YEAR_OVER_YEAR, label="year_over_year")

        match = re.search(r"前\s*(\d+)\s*天", text)
        if match and any(token in text for token in ["比", "比较", "对比"]):
            days = int(match.group(1))
            return TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD, requested_days=days, label=f"previous_{days}_days")
        if any(marker.lower() in low for marker in markers.get("previous_period", [])):
            return TimeComparisonContext(ComparisonMode.PREVIOUS_PERIOD, label="previous_period")
        return None

    def _is_change_request(self, text: str) -> bool:
        """判断 follow-up 是否明确要求修改当前分析上下文。"""
        low = text.lower()
        return any(marker.lower() in low for marker in self.time_policy.get("markers", {}).get("change_request", []))

    @staticmethod
    def _is_clear_comparison(text: str) -> bool:
        """判断 follow-up 是否明确要求清除 comparison 设置。"""
        low = text.lower()
        return any(marker in low for marker in ["取消对比", "取消比较", "不比了", "清除对比", "clear comparison"])

    def _metric_delta(self, spec: SemanticQuerySpec, text: str):
        """解析有限 Metric 变更；不允许静默切换到未治理 Metric。"""
        add_markers = ["再加上", "加上", "再加", "also add", "add "]
        if not any(m.lower() in text.lower() for m in add_markers):
            return None
        found = []
        low = text.lower()
        for metric, aliases in METRIC_ALIASES.items():
            if any(a.lower() in low for a in aliases):
                found.append(metric)
        found = [m for m in dict.fromkeys(found) if m not in spec.metric_names]
        if len(found) != 1:
            return None
        metrics = tuple([*spec.metric_names, found[0]])
        return (
            SessionDeltaKind.ADD_METRIC,
            replace(spec, metrics=metrics),
            f"Added governed metric {found[0]} while preserving time/grain/filters.",
        )

    def _remove_filter_delta(self, spec, text):
        """解析明确的过滤删除请求，只能删除当前会话已经存在的受治理 filter。"""
        if not any(m in text.lower() for m in ["去掉", "取消", "remove", "不要"]):
            return None
        matches = []
        for dim, cfg in self.semantic_policy["structured_filter_dimensions"].items():
            aliases = [dim, *cfg.get("dimension_aliases", [])]
            if any(a.lower() in text.lower() for a in aliases):
                matches.append(dim)
        matches = list(dict.fromkeys(matches))
        if len(matches) != 1:
            return None
        dim = matches[0]
        if not any(f.dimension == dim for f in spec.filters):
            return None
        filters = tuple(f for f in spec.filters if f.dimension != dim)
        return (
            SessionDeltaKind.REMOVE_FILTER,
            replace(spec, filters=filters),
            f"Removed filter {dim} while preserving the rest of the analysis state.",
        )

    def _filter_delta(self, spec, text):
        """解析新增/替换过滤条件并交给治理解析器验证。"""
        raw = self._extract_followup_value(text)
        if not raw:
            return None
        result = self.resolver.resolve(metrics=spec.metric_names, raw_value=raw, question=text)
        if result.status is not DimensionResolutionStatus.RESOLVED:
            return None
        dim = result.resolved_dimension
        value = result.resolved_value
        if not dim or value is None:
            return None
        filt = SemanticDimensionFilter(
            dim,
            SemanticFilterOperator.EQ,
            value,
            source=f"session_followup:{result.mode.value}:{result.evidence}",
        )
        existing = [f for f in spec.filters if f.dimension == dim]
        if existing:
            filters = tuple(filt if f.dimension == dim else f for f in spec.filters)
            kind = SessionDeltaKind.REPLACE_FILTER
            verb = "Replaced"
        else:
            filters = tuple([*spec.filters, filt])
            kind = SessionDeltaKind.ADD_FILTER
            verb = "Added"
        return kind, replace(spec, filters=filters), f"{verb} filter {dim}={value} while preserving metric/time/grain."

    def _extract_followup_value(self, text):
        """从 follow-up 中提取明确的候选值文本，供受治理 Dimension Value Resolution 使用。"""
        for marker in ["只看", "仅看", "only"]:
            idx = text.lower().find(marker.lower())
            if idx >= 0:
                raw = text[idx + len(marker):].strip(" ，,。！？?")
                raw = re.sub(r"(呢|吧|好吗|可以吗)$", "", raw).strip()
                if raw:
                    return raw
        hits = []
        for _, cfg in self.semantic_policy["structured_filter_dimensions"].items():
            for canonical, aliases in cfg.get("value_aliases", {}).items():
                for alias in [canonical, *aliases]:
                    if re.search(r"[A-Za-z]", alias):
                        matched = re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.I)
                    else:
                        matched = alias in text
                    if matched:
                        hits.append(canonical)
                        break
        hits = list(dict.fromkeys(hits))
        return hits[0] if len(hits) == 1 else None

    def _state(
        self,
        *,
        session_id,
        original_question,
        spec,
        revision,
        turn_count,
        last_question,
        history,
        comparison,
    ):
        """集中构造 AnalysisSessionState，保证 fingerprint、checksum 与 turn 序列一致。"""
        payload = self._fingerprint(
            session_id,
            original_question,
            spec,
            revision,
            turn_count,
            last_question,
            history,
            comparison,
        )
        return AnalysisSessionState(
            session_id=session_id,
            contract_version=int(self.policy["version"]),
            original_question=original_question,
            current_spec=spec,
            revision=revision,
            turn_count=turn_count,
            last_question=last_question,
            history=history,
            integrity_checksum=self._checksum(payload),
            comparison=comparison,
        )

    def _validate(self, state):
        """验证恢复/更新后的 Session checksum、fingerprint 与受限字段，防止上下文被篡改。"""
        if state.contract_version != int(self.policy["version"]):
            return "Analysis-session contract version mismatch."
        expected = self._checksum(
            self._fingerprint(
                state.session_id,
                state.original_question,
                state.current_spec,
                state.revision,
                state.turn_count,
                state.last_question,
                state.history,
                state.comparison,
            )
        )
        if expected != state.integrity_checksum:
            return "Analysis-session integrity checksum mismatch; refuse mutated state."
        if state.revision != state.turn_count:
            return "Analysis-session revision/turn count mismatch."
        return None

    @staticmethod
    def _fingerprint(session_id, original_question, spec, revision, turn_count, last_question, history, comparison):
        """对会话中具有业务语义的查询状态计算稳定 SHA-256 fingerprint。"""
        return {
            "session_id": session_id,
            "original_question": original_question,
            "spec": spec.to_dict(),
            "revision": revision,
            "turn_count": turn_count,
            "last_question": last_question,
            "history": [x.to_dict() for x in history],
            "comparison": comparison.to_dict() if comparison else None,
        }

    @staticmethod
    def _checksum(payload):
        """对完整序列化会话状态计算 checksum，用于检测持久化内容变化。"""
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @staticmethod
    def _contains_raw_query_surface(text):
        """检测 follow-up 是否试图注入 SQL / raw MetricFlow 参数等越权查询表面。"""
        return "=" in text or bool(re.search(r"\b(select|where|delete|drop|truncate|update|insert)\b", text, re.I))

    def _session_runtime_enabled(self) -> bool:
        """读取 Analysis Session Runtime permission gate；默认关闭。"""
        return os.getenv(self.policy["runtime"]["allow_env"], "false").lower() == "true"

    def _session_gate_warning(self) -> str:
        """生成 Runtime gate 未开启时的明确 DEFERRED/blocked 说明。"""
        gate = self.policy["runtime"]["allow_env"]
        return f"Session execution is disabled; set {gate}=true only in the intended runtime."

    @staticmethod
    def _map_status(status: SemanticQueryStatus) -> AnalysisSessionStatus:
        """把底层 Semantic/Comparison 状态投影为 Analysis Session 状态，不夸大底层证据等级。"""
        return {
            SemanticQueryStatus.COMPLETE: AnalysisSessionStatus.COMPLETE,
            SemanticQueryStatus.DEFERRED: AnalysisSessionStatus.DEFERRED,
            SemanticQueryStatus.BLOCKED: AnalysisSessionStatus.BLOCKED,
            SemanticQueryStatus.ERROR: AnalysisSessionStatus.ERROR,
            SemanticQueryStatus.CLARIFICATION_REQUIRED: AnalysisSessionStatus.CLARIFICATION_REQUIRED,
            SemanticQueryStatus.READY: AnalysisSessionStatus.READY,
        }[status]
