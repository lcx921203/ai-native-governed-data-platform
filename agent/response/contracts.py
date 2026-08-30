from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AnswerStatus(str, Enum):
    ANSWERED = "ANSWERED"
    PARTIAL = "PARTIAL"
    NEEDS_DISCOVERY = "NEEDS_DISCOVERY"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


class ClaimKind(str, Enum):
    DEFINITION = "DEFINITION"
    FORMULA = "FORMULA"
    RELATIONSHIP = "RELATIONSHIP"
    GOVERNANCE = "GOVERNANCE"
    LINEAGE = "LINEAGE"
    AUTOMATION_CONTRACT = "AUTOMATION_CONTRACT"
    SEMANTIC_QUERY_PLAN = "SEMANTIC_QUERY_PLAN"
    QUERY_RESULT = "QUERY_RESULT"
    DISCOVERY = "DISCOVERY"
    LIMITATION = "LIMITATION"
    CLARIFICATION_REQUEST = "CLARIFICATION_REQUEST"
    SESSION_STATE = "SESSION_STATE"
    ANOMALY_OBSERVATION = "ANOMALY_OBSERVATION"
    OPERATIONAL_HEALTH = "OPERATIONAL_HEALTH"
    DIAGNOSTIC_CLASSIFICATION = "DIAGNOSTIC_CLASSIFICATION"
    DRIVER_ATTRIBUTION = "DRIVER_ATTRIBUTION"
    INCIDENT_EVIDENCE = "INCIDENT_EVIDENCE"
    RECOVERY_STATUS = "RECOVERY_STATUS"
    INCIDENT_RESPONSE_PLAN = "INCIDENT_RESPONSE_PLAN"
    ACTION_AUTHORITY = "ACTION_AUTHORITY"
    APPROVAL_STATUS = "APPROVAL_STATUS"
    APPROVAL_AUDIT = "APPROVAL_AUDIT"
    KNOWLEDGE_EVIDENCE = "KNOWLEDGE_EVIDENCE"


@dataclass(frozen=True)
class Claim:
    id: str
    kind: ClaimKind
    text: str
    evidence: str = "STATIC_CONTRACT"
    source_locations: tuple[str, ...] = ()
    runtime_observed: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind.value,
            "text": self.text,
            "evidence": self.evidence,
            "source_locations": list(self.source_locations),
            "runtime_observed": self.runtime_observed,
        }


@dataclass
class ResponseEnvelope:
    question: str
    intent: str
    status: AnswerStatus
    subject: dict[str, Any] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    evidence_levels: list[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "question": self.question,
            "intent": self.intent,
            "status": self.status.value,
            "subject": self.subject,
            "claims": [c.to_dict() for c in self.claims],
            "limitations": list(self.limitations),
            "sources": self.sources,
            "tool_trace": self.tool_trace,
            "evidence_levels": self.evidence_levels,
        }


@dataclass(frozen=True)
class AnswerDraft:
    answer: str
    used_claim_ids: tuple[str, ...] = ()
    acknowledged_limitations: tuple[str, ...] = ()
