from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IncidentResponseStatus(str, Enum):
    NO_ACTION = "NO_ACTION"
    WAITING = "WAITING"
    DELEGATED = "DELEGATED"
    HUMAN_ACTION_REQUIRED = "HUMAN_ACTION_REQUIRED"
    PARTIAL = "PARTIAL"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class ResponseAuthority(str, Enum):
    NONE = "NONE"
    DAGSTER_RECOVERY_SENSOR = "DAGSTER_RECOVERY_SENSOR"
    DAGSTER_RUN_OWNER = "DAGSTER_RUN_OWNER"
    HUMAN_DATA_OPERATOR = "HUMAN_DATA_OPERATOR"
    PLATFORM_OPERATOR = "PLATFORM_OPERATOR"


class ApprovalBoundary(str, Enum):
    NONE = "NONE"
    AUTOMATION_POLICY_OWNED = "AUTOMATION_POLICY_OWNED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class ResponseActionKind(str, Enum):
    CLOSE_INCIDENT = "CLOSE_INCIDENT"
    WAIT_FOR_FRESHNESS_DEADLINE = "WAIT_FOR_FRESHNESS_DEADLINE"
    WAIT_FOR_ACTIVE_RUN = "WAIT_FOR_ACTIVE_RUN"
    WAIT_FOR_ACTIVE_RECOVERY = "WAIT_FOR_ACTIVE_RECOVERY"
    DELEGATE_AUTO_REPLAY = "DELEGATE_AUTO_REPLAY"
    VERIFY_EXACT_PARTITION_COMPLETION = "VERIFY_EXACT_PARTITION_COMPLETION"
    RESTORE_INFRASTRUCTURE = "RESTORE_INFRASTRUCTURE"
    REEVALUATE_RECOVERY_POLICY = "REEVALUATE_RECOVERY_POLICY"
    INVESTIGATE_DATA_CONTRACT = "INVESTIGATE_DATA_CONTRACT"
    FIX_DETERMINISTIC_CODE = "FIX_DETERMINISTIC_CODE"
    INVESTIGATE_UNKNOWN_FAILURE = "INVESTIGATE_UNKNOWN_FAILURE"
    INVESTIGATE_REPLAY_EXHAUSTION = "INVESTIGATE_REPLAY_EXHAUSTION"
    VALIDATE_SUCCESS_WITH_INCOMPLETE_PARTITION = "VALIDATE_SUCCESS_WITH_INCOMPLETE_PARTITION"
    REVIEW_HISTORICAL_NO_RUN = "REVIEW_HISTORICAL_NO_RUN"
    APPROVE_MANUAL_BACKFILL = "APPROVE_MANUAL_BACKFILL"
    MANUAL_INCIDENT_REVIEW = "MANUAL_INCIDENT_REVIEW"


@dataclass(frozen=True)
class IncidentResponseStep:
    sequence: int
    action: ResponseActionKind
    authority: ResponseAuthority
    approval_boundary: ApprovalBoundary
    rationale: str
    executable_by_agent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "action": self.action.value,
            "authority": self.authority.value,
            "approval_boundary": self.approval_boundary.value,
            "rationale": self.rationale,
            "executable_by_agent": self.executable_by_agent,
        }


@dataclass(frozen=True)
class PartitionResponsePlan:
    partition_key: str
    status: IncidentResponseStatus
    policy_action: str
    policy_reason: str
    steps: tuple[IncidentResponseStep, ...]

    @property
    def human_approval_required(self) -> bool:
        return any(step.approval_boundary is ApprovalBoundary.HUMAN_REQUIRED for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition_key": self.partition_key,
            "status": self.status.value,
            "policy_action": self.policy_action,
            "policy_reason": self.policy_reason,
            "human_approval_required": self.human_approval_required,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass
class IncidentResponsePlan:
    status: IncidentResponseStatus
    evidence: str
    partitions: tuple[PartitionResponsePlan, ...] = ()
    warnings: list[str] = field(default_factory=list)
    validation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "evidence": self.evidence,
            "partitions": [item.to_dict() for item in self.partitions],
            "warnings": list(self.warnings),
            "validation": self.validation,
        }
