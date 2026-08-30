from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.semantic_query import SemanticQueryResult, SemanticQuerySpec, SemanticQueryStatus


class AnomalyState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNRESOLVED = "UNRESOLVED"


class AnomalyDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"


class OperationalHealthState(str, Enum):
    HEALTHY = "HEALTHY"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class SignalCauseClass(str, Enum):
    NO_ANOMALY = "NO_ANOMALY"
    BUSINESS_SIGNAL_SUSPECTED = "BUSINESS_SIGNAL_SUSPECTED"
    DATA_PIPELINE_SUSPECTED = "DATA_PIPELINE_SUSPECTED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class OperationalHealthSnapshot:
    state: OperationalHealthState
    evidence: str
    source: str = "dagster_runtime"
    details: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "evidence": self.evidence,
            "source": self.source,
            "details": self.details,
        }


@dataclass(frozen=True)
class AnomalyBaselineWindow:
    index: int
    spec: SemanticQuerySpec

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "spec": self.spec.to_dict()}


@dataclass
class DriverAnalysisPlan:
    status: SemanticQueryStatus
    metric: str
    current_spec: SemanticQuerySpec
    reference_spec: SemanticQuerySpec | None
    dimensions: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "metric": self.metric,
            "current_spec": self.current_spec.to_dict(),
            "reference_spec": self.reference_spec.to_dict() if self.reference_spec else None,
            "dimensions": list(self.dimensions),
            "warnings": list(self.warnings),
        }


@dataclass
class AnomalyDetectionPlan:
    status: SemanticQueryStatus
    question: str
    metric: str | None = None
    current_spec: SemanticQuerySpec | None = None
    baseline_windows: tuple[AnomalyBaselineWindow, ...] = ()
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "question": self.question,
            "metric": self.metric,
            "current_spec": self.current_spec.to_dict() if self.current_spec else None,
            "baseline_windows": [window.to_dict() for window in self.baseline_windows],
            "warnings": list(self.warnings),
        }


@dataclass
class AnomalyDetectionResult:
    status: SemanticQueryStatus
    evidence: str
    plan: AnomalyDetectionPlan
    anomaly_state: AnomalyState = AnomalyState.UNRESOLVED
    direction: AnomalyDirection = AnomalyDirection.UNKNOWN
    cause_class: SignalCauseClass = SignalCauseClass.UNRESOLVED
    current_value: str | None = None
    baseline_value: str | None = None
    absolute_change: str | None = None
    relative_change_percent: str | None = None
    baseline_values: tuple[str, ...] = ()
    reference_window_index: int | None = None
    current_result: SemanticQueryResult | None = None
    baseline_results: tuple[SemanticQueryResult, ...] = ()
    operational_health: OperationalHealthSnapshot | None = None
    driver_plan: DriverAnalysisPlan | None = None
    warnings: list[str] = field(default_factory=list)
    validation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "plan": self.plan.to_dict(),
            "anomaly_state": self.anomaly_state.value,
            "direction": self.direction.value,
            "cause_class": self.cause_class.value,
            "current_value": self.current_value,
            "baseline_value": self.baseline_value,
            "absolute_change": self.absolute_change,
            "relative_change_percent": self.relative_change_percent,
            "baseline_values": list(self.baseline_values),
            "reference_window_index": self.reference_window_index,
            "operational_health": self.operational_health.to_dict() if self.operational_health else None,
            "driver_plan": self.driver_plan.to_dict() if self.driver_plan else None,
            "warnings": list(self.warnings),
            "validation": self.validation,
            "current_result": self.current_result.to_dict() if self.current_result else None,
            "baseline_results": [item.to_dict() for item in self.baseline_results],
        }
