from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.anomaly_analysis import AnomalyDirection
from agent.semantic_query import SemanticQueryResult, SemanticQuerySpec, SemanticQueryStatus


class DriverAttributionStatus(str, Enum):
    READY = "READY"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class DriverLensPlan:
    dimension: str
    current_spec: SemanticQuerySpec
    reference_spec: SemanticQuerySpec
    additive: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "current_spec": self.current_spec.to_dict(),
            "reference_spec": self.reference_spec.to_dict(),
            "additive": self.additive,
        }


@dataclass
class DriverAttributionPlan:
    status: DriverAttributionStatus
    metric: str | None = None
    direction: AnomalyDirection = AnomalyDirection.UNKNOWN
    lenses: tuple[DriverLensPlan, ...] = ()
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "metric": self.metric,
            "direction": self.direction.value,
            "lenses": [item.to_dict() for item in self.lenses],
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DriverAttributionRow:
    dimension: str
    dimension_value: str
    metric: str
    current_value: str | None
    reference_value: str | None
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
            "reference_value": self.reference_value,
            "absolute_change": self.absolute_change,
            "growth_rate_percent": self.growth_rate_percent,
            "contribution_percent": self.contribution_percent,
            "rank": self.rank,
        }


@dataclass
class DriverLensResult:
    dimension: str
    status: DriverAttributionStatus
    evidence: str
    additive: bool
    rows: list[DriverAttributionRow] = field(default_factory=list)
    current_result: SemanticQueryResult | None = None
    reference_result: SemanticQueryResult | None = None
    warnings: list[str] = field(default_factory=list)
    validation: str = ""

    @property
    def strongest_driver(self) -> DriverAttributionRow | None:
        return self.rows[0] if self.rows else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "status": self.status.value,
            "evidence": self.evidence,
            "additive": self.additive,
            "rows": [row.to_dict() for row in self.rows],
            "strongest_driver": self.strongest_driver.to_dict() if self.strongest_driver else None,
            "warnings": list(self.warnings),
            "validation": self.validation,
            "current_result": self.current_result.to_dict() if self.current_result else None,
            "reference_result": self.reference_result.to_dict() if self.reference_result else None,
        }


@dataclass
class DriverAttributionResult:
    status: DriverAttributionStatus
    evidence: str
    plan: DriverAttributionPlan
    lenses: list[DriverLensResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "plan": self.plan.to_dict(),
            "lenses": [item.to_dict() for item in self.lenses],
            "strongest_driver_by_dimension": {
                item.dimension: item.strongest_driver.to_dict()
                for item in self.lenses
                if item.status is DriverAttributionStatus.COMPLETE and item.strongest_driver is not None
            },
            "warnings": list(self.warnings),
            "validation": self.validation,
        }
