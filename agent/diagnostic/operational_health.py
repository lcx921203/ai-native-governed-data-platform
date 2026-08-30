from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from agent.anomaly_analysis import OperationalHealthSnapshot, OperationalHealthState
from agent.semantic_query import SemanticQuerySpec


class OperationalHealthProvider(Protocol):
    def snapshot(self, spec: SemanticQuerySpec) -> OperationalHealthSnapshot: ...


class DeferredOperationalHealthProvider:
    def snapshot(self, spec: SemanticQuerySpec) -> OperationalHealthSnapshot:
        return OperationalHealthSnapshot(
            state=OperationalHealthState.UNKNOWN,
            evidence="DEFERRED",
            source="dagster_exact_partition_completeness",
            details="Real Dagster exact-partition completeness evidence is not available in this runtime.",
        )


class DagsterPartitionCompletenessHealthProvider:
    """Read current exact-partition truth from Dagster event/run storage.

    Health is intentionally based on current consumer completeness, not merely latest Run status:
    - all queried daily partitions materialized for all Phase 3C consumer marts -> HEALTHY;
    - any overdue queried daily partition incomplete -> UNHEALTHY;
    - otherwise -> UNKNOWN.

    Dagster imports are lazy so static/mobile development remains importable without Dagster installed.
    """

    def __init__(self, project_root: Path | str, *, instance=None, now_provider=None):
        self.root = Path(project_root).resolve()
        self.instance = instance
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def snapshot(self, spec: SemanticQuerySpec) -> OperationalHealthSnapshot:
        try:
            import dagster as dg  # type: ignore
            from orchestration.dagster.commerce_dagster.automation_policy import (
                missed_schedule_auto_replay_eligible,
                partition_deadline_utc,
            )
            from orchestration.dagster.commerce_dagster.recovery_state import (
                collect_partition_recovery_state,
            )
        except Exception as exc:
            return self._deferred(f"Dagster runtime modules are unavailable: {exc}")

        try:
            instance = self.instance or dg.DagsterInstance.get()
        except Exception as exc:
            return self._deferred(f"Dagster instance is unavailable: {exc}")

        try:
            start = self._parse(spec.start_time).date()
            end = self._parse(spec.end_time).date()
        except Exception as exc:
            return self._deferred(f"Cannot map semantic-query time window to Dagster daily partitions: {exc}")

        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        incomplete_overdue = []
        incomplete_not_due = []
        inspected = []
        current = start
        try:
            while current <= end:
                key = current.isoformat()
                overdue = partition_deadline_utc(key) <= now
                state = collect_partition_recovery_state(
                    instance,
                    partition_key=key,
                    freshness_overdue=overdue,
                    infrastructure_healthy=True,
                    missed_schedule_eligible=missed_schedule_auto_replay_eligible(key, now),
                )
                inspected.append(key)
                if state.missing_mart_asset_keys:
                    item = f"{key}:missing={','.join(state.missing_mart_asset_keys)}"
                    (incomplete_overdue if overdue else incomplete_not_due).append(item)
                current += timedelta(days=1)
        except Exception as exc:
            return self._deferred(f"Dagster exact-partition state read failed: {exc}")

        if incomplete_overdue:
            return OperationalHealthSnapshot(
                state=OperationalHealthState.UNHEALTHY,
                evidence="RUNTIME_VERIFIED",
                source="dagster_exact_partition_completeness",
                details="Overdue incomplete partition(s): " + "; ".join(incomplete_overdue),
            )
        if incomplete_not_due:
            return OperationalHealthSnapshot(
                state=OperationalHealthState.UNKNOWN,
                evidence="RUNTIME_VERIFIED",
                source="dagster_exact_partition_completeness",
                details="Queried partition(s) are not complete but their freshness deadline has not passed: "
                + "; ".join(incomplete_not_due),
            )
        return OperationalHealthSnapshot(
            state=OperationalHealthState.HEALTHY,
            evidence="RUNTIME_VERIFIED",
            source="dagster_exact_partition_completeness",
            details="All queried exact daily partitions are complete: " + ", ".join(inspected),
        )

    @staticmethod
    def _parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

    @staticmethod
    def _deferred(detail: str) -> OperationalHealthSnapshot:
        return OperationalHealthSnapshot(
            state=OperationalHealthState.UNKNOWN,
            evidence="DEFERRED",
            source="dagster_exact_partition_completeness",
            details=detail,
        )
