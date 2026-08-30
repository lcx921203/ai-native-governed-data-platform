from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.semantic_query import SemanticQueryResult, SemanticQuerySpec, SemanticQueryStatus
from agent.time_context import TimeComparisonContext


class BreakdownAnalysisMode(str, Enum):
    COMPARE = "COMPARE"
    TOP_ABSOLUTE_CHANGE = "TOP_ABSOLUTE_CHANGE"
    TOP_GROWTH_RATE = "TOP_GROWTH_RATE"
    CONTRIBUTION = "CONTRIBUTION"


@dataclass
class ComparativeBreakdownPlan:
    status: SemanticQueryStatus
    question: str
    mode: BreakdownAnalysisMode
    context: TimeComparisonContext | None = None
    dimension: str | None = None
    current_spec: SemanticQuerySpec | None = None
    comparison_spec: SemanticQuerySpec | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "question": self.question,
            "mode": self.mode.value,
            "context": self.context.to_dict() if self.context else None,
            "dimension": self.dimension,
            "current_spec": self.current_spec.to_dict() if self.current_spec else None,
            "comparison_spec": self.comparison_spec.to_dict() if self.comparison_spec else None,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ComparativeBreakdownRow:
    dimension: str
    dimension_value: str
    metric: str
    current_value: str | None
    comparison_value: str | None
    absolute_change: str | None
    growth_rate_percent: str | None
    contribution_percent: str | None = None
    rank: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "dimension_value": self.dimension_value,
            "metric": self.metric,
            "current_value": self.current_value,
            "comparison_value": self.comparison_value,
            "absolute_change": self.absolute_change,
            "growth_rate_percent": self.growth_rate_percent,
            "contribution_percent": self.contribution_percent,
            "rank": self.rank,
        }


@dataclass
class ComparativeBreakdownResult:
    status: SemanticQueryStatus
    evidence: str
    plan: ComparativeBreakdownPlan
    rows: list[ComparativeBreakdownRow] = field(default_factory=list)
    current_result: SemanticQueryResult | None = None
    comparison_result: SemanticQueryResult | None = None
    aggregate_current_result: SemanticQueryResult | None = None
    aggregate_comparison_result: SemanticQueryResult | None = None
    warnings: list[str] = field(default_factory=list)
    validation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "plan": self.plan.to_dict(),
            "rows": [row.to_dict() for row in self.rows],
            "warnings": list(self.warnings),
            "validation": self.validation,
            "current_result": self.current_result.to_dict() if self.current_result else None,
            "comparison_result": self.comparison_result.to_dict() if self.comparison_result else None,
            "aggregate_current_result": self.aggregate_current_result.to_dict() if self.aggregate_current_result else None,
            "aggregate_comparison_result": self.aggregate_comparison_result.to_dict() if self.aggregate_comparison_result else None,
        }
