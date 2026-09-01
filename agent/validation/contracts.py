"""Analysis Validation 的结构化契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ValidationDecision(str, Enum):
    PASS = "PASS"
    RETRY = "RETRY"
    BLOCKED = "BLOCKED"


class ValidationSeverity(str, Enum):
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: ValidationSeverity
    unit_id: str | None = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "unit_id": self.unit_id,
            "retryable": self.retryable,
        }


@dataclass
class AnalysisValidationResult:
    decision: ValidationDecision
    issues: tuple[ValidationIssue, ...] = ()
    retry_unit_ids: tuple[str, ...] = ()
    checked_units: int = 0
    evidence: str = "STATIC_CONTRACT"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "issues": [item.to_dict() for item in self.issues],
            "retry_unit_ids": list(self.retry_unit_ids),
            "checked_units": self.checked_units,
            "evidence": self.evidence,
            "warnings": list(self.warnings),
        }
