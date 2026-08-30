from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.semantic_query.contracts import SemanticQueryPlan, SemanticQueryResult, SemanticQuerySpec
from agent.time_context.contracts import ComparativeQueryPlan, ComparativeQueryResult, TimeComparisonContext
from agent.breakdown_analysis.contracts import ComparativeBreakdownPlan, ComparativeBreakdownResult


class AnalysisSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    READY = "READY"
    COMPLETE = "COMPLETE"
    DEFERRED = "DEFERRED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class SessionDeltaKind(str, Enum):
    ADD_METRIC = "ADD_METRIC"
    ADD_FILTER = "ADD_FILTER"
    REPLACE_FILTER = "REPLACE_FILTER"
    REMOVE_FILTER = "REMOVE_FILTER"
    SET_COMPARISON = "SET_COMPARISON"
    CLEAR_COMPARISON = "CLEAR_COMPARISON"
    COMPUTE_COMPARISON = "COMPUTE_COMPARISON"
    COMPUTE_BREAKDOWN = "COMPUTE_BREAKDOWN"
    RANK_BREAKDOWN = "RANK_BREAKDOWN"
    CONTRIBUTION_ANALYSIS = "CONTRIBUTION_ANALYSIS"
    NOOP = "NOOP"


@dataclass(frozen=True)
class SessionTurn:
    revision: int
    question: str
    delta_kind: SessionDeltaKind
    summary: str

    def to_dict(self):
        return {
            "revision": self.revision,
            "question": self.question,
            "delta_kind": self.delta_kind.value,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class AnalysisSessionState:
    session_id: str
    contract_version: int
    original_question: str
    current_spec: SemanticQuerySpec
    revision: int
    turn_count: int
    last_question: str
    history: tuple[SessionTurn, ...]
    integrity_checksum: str
    comparison: TimeComparisonContext | None = None

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "contract_version": self.contract_version,
            "original_question": self.original_question,
            "current_spec": self.current_spec.to_dict(),
            "revision": self.revision,
            "turn_count": self.turn_count,
            "last_question": self.last_question,
            "history": [x.to_dict() for x in self.history],
            "integrity_checksum": self.integrity_checksum,
            "comparison": self.comparison.to_dict() if self.comparison else None,
        }


@dataclass
class AnalysisSessionResult:
    status: AnalysisSessionStatus
    state: AnalysisSessionState
    question: str
    delta_kind: SessionDeltaKind | None = None
    plan: SemanticQueryPlan | None = None
    query_result: SemanticQueryResult | None = None
    comparison_plan: ComparativeQueryPlan | None = None
    comparison_result: ComparativeQueryResult | None = None
    breakdown_plan: ComparativeBreakdownPlan | None = None
    breakdown_result: ComparativeBreakdownResult | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "state": self.state.to_dict(),
            "question": self.question,
            "delta_kind": self.delta_kind.value if self.delta_kind else None,
            "plan": self.plan.to_dict() if self.plan else None,
            "query_result": self.query_result.to_dict() if self.query_result else None,
            "comparison_plan": self.comparison_plan.to_dict() if self.comparison_plan else None,
            "comparison_result": self.comparison_result.to_dict() if self.comparison_result else None,
            "breakdown_plan": self.breakdown_plan.to_dict() if self.breakdown_plan else None,
            "breakdown_result": self.breakdown_result.to_dict() if self.breakdown_result else None,
            "warnings": list(self.warnings),
        }
