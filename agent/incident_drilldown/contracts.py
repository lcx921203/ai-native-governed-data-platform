from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IncidentDrilldownStatus(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NO_INCIDENT = "NO_INCIDENT"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class FailedRunEvidence:
    run_id: str
    status: str
    failure_class: str
    failure_source: str | None = None
    failure_component: str | None = None
    failure_reason: str | None = None
    failure_stage: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "failure_class": self.failure_class,
            "failure_source": self.failure_source,
            "failure_component": self.failure_component,
            "failure_reason": self.failure_reason,
            "failure_stage": self.failure_stage,
        }


@dataclass(frozen=True)
class RecoveryPolicySnapshot:
    action: str
    reason_code: str
    explanation: str
    observed_auto_replay_attempts: int
    active_run_ids: tuple[str, ...] = ()
    active_recovery_run_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason_code": self.reason_code,
            "explanation": self.explanation,
            "observed_auto_replay_attempts": self.observed_auto_replay_attempts,
            "active_run_ids": list(self.active_run_ids),
            "active_recovery_run_ids": list(self.active_recovery_run_ids),
        }


@dataclass(frozen=True)
class PartitionIncidentEvidence:
    partition_key: str
    freshness_overdue: bool
    exact_partition_complete: bool
    missing_mart_asset_keys: tuple[str, ...]
    run_ids: tuple[str, ...]
    failed_run_ids: tuple[str, ...]
    successful_run_ids: tuple[str, ...]
    latest_failed_run: FailedRunEvidence | None
    recovery: RecoveryPolicySnapshot
    infrastructure_healthy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition_key": self.partition_key,
            "freshness_overdue": self.freshness_overdue,
            "exact_partition_complete": self.exact_partition_complete,
            "missing_mart_asset_keys": list(self.missing_mart_asset_keys),
            "run_ids": list(self.run_ids),
            "failed_run_ids": list(self.failed_run_ids),
            "successful_run_ids": list(self.successful_run_ids),
            "latest_failed_run": self.latest_failed_run.to_dict() if self.latest_failed_run else None,
            "recovery": self.recovery.to_dict(),
            "infrastructure_healthy": self.infrastructure_healthy,
        }


@dataclass
class IncidentDrilldownResult:
    status: IncidentDrilldownStatus
    evidence: str
    partitions: tuple[PartitionIncidentEvidence, ...] = ()
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
