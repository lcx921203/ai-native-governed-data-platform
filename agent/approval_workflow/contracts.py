from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ApprovalWorkflowStatus(str, Enum):
    NO_APPROVAL_REQUIRED = "NO_APPROVAL_REQUIRED"
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalEventType(str, Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ApprovalActorType(str, Enum):
    HUMAN_OPERATOR = "HUMAN_OPERATOR"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"


class ApprovalAuthorizationStatus(str, Enum):
    ELIGIBLE_FOR_EXTERNAL_EXECUTION = "ELIGIBLE_FOR_EXTERNAL_EXECUTION"
    NOT_APPROVED = "NOT_APPROVED"
    EXPIRED = "EXPIRED"
    EVIDENCE_CHANGED = "EVIDENCE_CHANGED"
    ACTION_NO_LONGER_PRESENT = "ACTION_NO_LONGER_PRESENT"
    INVALID_AUDIT_CHAIN = "INVALID_AUDIT_CHAIN"


@dataclass(frozen=True)
class ApprovalActor:
    subject_id: str
    actor_type: ApprovalActorType
    authenticated: bool
    identity_source: str = "AUTHENTICATED_UPSTREAM"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "actor_type": self.actor_type.value,
            "authenticated": self.authenticated,
            "identity_source": self.identity_source,
        }


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    partition_key: str
    action: str
    authority: str
    policy_action: str
    policy_reason: str
    evidence_fingerprint: str
    request_hash: str
    requested_at: str
    expires_at: str
    execution_authorized_by_agent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "partition_key": self.partition_key,
            "action": self.action,
            "authority": self.authority,
            "policy_action": self.policy_action,
            "policy_reason": self.policy_reason,
            "evidence_fingerprint": self.evidence_fingerprint,
            "request_hash": self.request_hash,
            "requested_at": self.requested_at,
            "expires_at": self.expires_at,
            "execution_authorized_by_agent": self.execution_authorized_by_agent,
        }


@dataclass(frozen=True)
class ApprovalAuditEvent:
    sequence: int
    approval_id: str
    event_type: ApprovalEventType
    previous_status: str | None
    new_status: ApprovalStatus
    occurred_at: str
    actor: ApprovalActor
    reason: str
    request_hash: str
    previous_event_hash: str
    event_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "approval_id": self.approval_id,
            "event_type": self.event_type.value,
            "previous_status": self.previous_status,
            "new_status": self.new_status.value,
            "occurred_at": self.occurred_at,
            "actor": self.actor.to_dict(),
            "reason": self.reason,
            "request_hash": self.request_hash,
            "previous_event_hash": self.previous_event_hash,
            "event_hash": self.event_hash,
        }


@dataclass(frozen=True)
class ApprovalCase:
    request: ApprovalRequest
    events: tuple[ApprovalAuditEvent, ...]

    @property
    def status(self) -> ApprovalStatus:
        return self.events[-1].new_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "request": self.request.to_dict(),
            "status": self.status.value,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass
class ApprovalWorkflowBundle:
    status: ApprovalWorkflowStatus
    evidence: str
    cases: tuple[ApprovalCase, ...] = ()
    warnings: list[str] = field(default_factory=list)
    validation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "cases": [case.to_dict() for case in self.cases],
            "warnings": list(self.warnings),
            "validation": self.validation,
        }


@dataclass(frozen=True)
class ApprovalAuthorizationCheck:
    status: ApprovalAuthorizationStatus
    approval_id: str
    eligible_for_external_execution: bool
    agent_execution_allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "approval_id": self.approval_id,
            "eligible_for_external_execution": self.eligible_for_external_execution,
            "agent_execution_allowed": self.agent_execution_allowed,
            "reason": self.reason,
        }
