from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.anomaly_analysis import AnomalyDetectionResult, OperationalHealthSnapshot
from agent.driver_attribution import DriverAttributionResult
from agent.incident_drilldown import IncidentDrilldownResult
from agent.incident_response import IncidentResponsePlan
from agent.semantic_query import SemanticQueryPlan, SemanticQuerySpec, SemanticQueryStatus


class DiagnosticStatus(str, Enum):
    READY = "READY"
    NORMAL = "NORMAL"
    BUSINESS_DRIVERS_IDENTIFIED = "BUSINESS_DRIVERS_IDENTIFIED"
    DATA_PIPELINE_SUSPECTED = "DATA_PIPELINE_SUSPECTED"
    UNRESOLVED = "UNRESOLVED"
    PARTIAL = "PARTIAL"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass
class DiagnosticRequestPlan:
    status: SemanticQueryStatus
    question: str
    resolved_question: str
    metric: str | None = None
    semantic_plan: SemanticQueryPlan | None = None
    relative_time_resolution: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def spec(self) -> SemanticQuerySpec | None:
        return self.semantic_plan.spec if self.semantic_plan else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "question": self.question,
            "resolved_question": self.resolved_question,
            "metric": self.metric,
            "relative_time_resolution": self.relative_time_resolution,
            "semantic_plan": self.semantic_plan.to_dict() if self.semantic_plan else None,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class DiagnosticTraceStep:
    stage: str
    status: str
    evidence: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "status": self.status,
            "evidence": self.evidence,
            "detail": self.detail,
        }


@dataclass
class DiagnosticResult:
    status: DiagnosticStatus
    evidence: str
    plan: DiagnosticRequestPlan
    operational_health: OperationalHealthSnapshot | None = None
    anomaly: AnomalyDetectionResult | None = None
    attribution: DriverAttributionResult | None = None
    incident: IncidentDrilldownResult | None = None
    incident_response: IncidentResponsePlan | None = None
    trace: list[DiagnosticTraceStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    validation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "plan": self.plan.to_dict(),
            "operational_health": self.operational_health.to_dict() if self.operational_health else None,
            "anomaly": self.anomaly.to_dict() if self.anomaly else None,
            "attribution": self.attribution.to_dict() if self.attribution else None,
            "incident": self.incident.to_dict() if self.incident else None,
            "incident_response": self.incident_response.to_dict() if self.incident_response else None,
            "trace": [item.to_dict() for item in self.trace],
            "warnings": list(self.warnings),
            "validation": self.validation,
        }
