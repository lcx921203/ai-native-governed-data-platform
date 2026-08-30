from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.semantic_query.contracts import SemanticQueryResult, SemanticQuerySpec, SemanticQueryStatus


class ComparisonMode(str, Enum):
    PREVIOUS_PERIOD = "PREVIOUS_PERIOD"
    YEAR_OVER_YEAR = "YEAR_OVER_YEAR"


@dataclass(frozen=True)
class TimeComparisonContext:
    mode: ComparisonMode
    requested_days: int | None = None
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "requested_days": self.requested_days,
            "label": self.label,
        }


@dataclass
class ComparativeQueryPlan:
    status: SemanticQueryStatus
    question: str
    context: TimeComparisonContext | None = None
    current_spec: SemanticQuerySpec | None = None
    comparison_spec: SemanticQuerySpec | None = None
    outputs: tuple[str, ...] = (
        "current_value",
        "comparison_value",
        "absolute_change",
        "growth_rate_percent",
    )
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "question": self.question,
            "context": self.context.to_dict() if self.context else None,
            "current_spec": self.current_spec.to_dict() if self.current_spec else None,
            "comparison_spec": self.comparison_spec.to_dict() if self.comparison_spec else None,
            "outputs": list(self.outputs),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ComparativeMetricRow:
    metric: str
    current_value: str
    comparison_value: str
    absolute_change: str
    growth_rate_percent: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current_value": self.current_value,
            "comparison_value": self.comparison_value,
            "absolute_change": self.absolute_change,
            "growth_rate_percent": self.growth_rate_percent,
        }


@dataclass
class ComparativeQueryResult:
    status: SemanticQueryStatus
    evidence: str
    plan: ComparativeQueryPlan
    rows: list[ComparativeMetricRow] = field(default_factory=list)
    current_result: SemanticQueryResult | None = None
    comparison_result: SemanticQueryResult | None = None
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
        }
