from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Intent(str, Enum):
    METRIC_QUERY = "METRIC_QUERY"
    METRIC_DEFINITION = "METRIC_DEFINITION"
    ANALYSIS = "ANALYSIS"
    ENTITY_CONTEXT = "ENTITY_CONTEXT"
    DATASET_GOVERNANCE = "DATASET_GOVERNANCE"
    LINEAGE_QUERY = "LINEAGE_QUERY"
    RUNTIME_DIAGNOSIS = "RUNTIME_DIAGNOSIS"
    METADATA_DISCOVERY = "METADATA_DISCOVERY"
    DIMENSION_VALUE_DISCOVERY = "DIMENSION_VALUE_DISCOVERY"
    KNOWLEDGE_QUERY = "KNOWLEDGE_QUERY"
    UNKNOWN = "UNKNOWN"


class PlanStatus(str, Enum):
    PLANNED = "PLANNED"
    PLANNING_REQUIRED = "PLANNING_REQUIRED"
    BLOCKED = "BLOCKED"
    NEEDS_DISCOVERY = "NEEDS_DISCOVERY"


class ExecutionStatus(str, Enum):
    COMPLETE = "COMPLETE"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NEEDS_DISCOVERY = "NEEDS_DISCOVERY"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class ToolStep:
    tool: str
    arguments: dict[str, Any]
    purpose: str = ""
    stop_on_status: tuple[str, ...] = (
        "BLOCKED",
        "ERROR",
        "DEFERRED",
        "CLARIFICATION_REQUIRED",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "purpose": self.purpose,
            "stop_on_status": list(self.stop_on_status),
        }


@dataclass
class ToolPlan:
    question: str
    intent: Intent
    status: PlanStatus
    target_kind: str = ""
    target_id: str | None = ""
    target_match: str | None = ""
    steps: list[ToolStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "intent": self.intent.value,
            "status": self.status.value,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "target_match": self.target_match,
            "steps": [step.to_dict() for step in self.steps],
            "warnings": list(self.warnings),
        }


@dataclass
class PlanExecution:
    """受治理 ToolPlan 的执行结果。

    `substage_timings` 只供内部 Observability/Audit 使用，不进入 `to_dict()`，
    避免把工具内部性能结构变成公共业务响应的一部分。
    """

    plan: ToolPlan
    status: ExecutionStatus
    results: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    substage_timings: tuple[tuple[str, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "status": self.status.value,
            "results": self.results,
            "warnings": list(self.warnings),
        }
