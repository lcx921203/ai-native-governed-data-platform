from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from agent.semantic_query import SemanticQuerySpec

from .contracts import (
    FailedRunEvidence,
    IncidentDrilldownResult,
    IncidentDrilldownStatus,
    PartitionIncidentEvidence,
    RecoveryPolicySnapshot,
)


class IncidentRuntimeProvider(Protocol):
    def inspect(self, spec: SemanticQuerySpec) -> IncidentDrilldownResult: ...


class DeferredIncidentRuntimeProvider:
    def inspect(self, spec: SemanticQuerySpec) -> IncidentDrilldownResult:
        return IncidentDrilldownResult(
            status=IncidentDrilldownStatus.DEFERRED,
            evidence="DEFERRED",
            warnings=["Real Dagster Run Storage / exact-partition incident evidence is unavailable."],
            validation="DAGSTER_RUNTIME_UNAVAILABLE",
        )


class DagsterIncidentRuntimeProvider:
    """Read Phase 3C current truth without reimplementing Phase 3C policy.

    Runtime facts come from `collect_partition_recovery_state`. The *current policy
    decision* comes from `decide_recovery`. Structured failure tags are read from the
    latest failed run. Free-text log messages are deliberately not parsed for cause.
    """

    def __init__(self, project_root: Path | str, *, instance=None, now_provider=None, infrastructure_provider=None):
        self.root = Path(project_root).resolve()
        self.instance = instance
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.infrastructure_provider = infrastructure_provider

    def inspect(self, spec: SemanticQuerySpec) -> IncidentDrilldownResult:
        gate = "PHASE6D_ALLOW_INCIDENT_DRILLDOWN"
        if os.getenv(gate, "false").lower() != "true":
            return IncidentDrilldownResult(
                status=IncidentDrilldownStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                warnings=[f"Incident drilldown is disabled; set {gate}=true only in the intended runtime environment."],
                validation="NOT_EXECUTED",
            )

        try:
            import dagster as dg  # type: ignore
            from orchestration.dagster.commerce_dagster.automation_policy import (
                SHOPIFY_RECOVERY_REQUIRED_RUNTIME_SERVICES,
                missed_schedule_auto_replay_eligible,
                partition_deadline_utc,
            )
            from orchestration.dagster.commerce_dagster.failure_classification import (
                FAILURE_CLASS_SOURCE_TAG,
                FAILURE_COMPONENT_TAG,
                FAILURE_REASON_TAG,
                FAILURE_STAGE_TAG,
            )
            from orchestration.dagster.commerce_dagster.recovery_policy import decide_recovery
            from orchestration.dagster.commerce_dagster.recovery_state_current import (
                AUTO_RECOVERY_TAG_VALUE,
                RECOVERY_TAG,
                collect_partition_recovery_state,
            )
            from orchestration.dagster.commerce_dagster.runtime_health import docker_compose_services_running
        except Exception as exc:
            return self._deferred(f"Dagster / Phase 3C runtime modules are unavailable: {exc}")

        try:
            instance = self.instance or dg.DagsterInstance.get()
        except Exception as exc:
            return self._deferred(f"Dagster instance is unavailable: {exc}")

        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        try:
            start = self._parse(spec.start_time).date()
            end = self._parse(spec.end_time).date()
        except Exception as exc:
            return self._deferred(f"Cannot map query time window to Dagster daily partitions: {exc}")

        if self.infrastructure_provider is not None:
            infrastructure_healthy = bool(self.infrastructure_provider())
        else:
            try:
                infrastructure_healthy = docker_compose_services_running(
                    self.root,
                    SHOPIFY_RECOVERY_REQUIRED_RUNTIME_SERVICES,
                )
            except Exception:
                infrastructure_healthy = False

        partitions: list[PartitionIncidentEvidence] = []
        warnings: list[str] = []
        current = start
        try:
            while current <= end:
                key = current.isoformat()
                overdue = partition_deadline_utc(key) <= now
                runtime_state = collect_partition_recovery_state(
                    instance,
                    partition_key=key,
                    freshness_overdue=overdue,
                    infrastructure_healthy=infrastructure_healthy,
                    missed_schedule_eligible=missed_schedule_auto_replay_eligible(key, now),
                )
                decision = decide_recovery(runtime_state.observation)
                latest_failed = self._latest_failed_run_evidence(
                    instance,
                    runtime_state.latest_failed_run_id,
                    source_tag=FAILURE_CLASS_SOURCE_TAG,
                    component_tag=FAILURE_COMPONENT_TAG,
                    reason_tag=FAILURE_REASON_TAG,
                    stage_tag=FAILURE_STAGE_TAG,
                )
                active_recovery_ids = tuple(
                    run_id
                    for run_id in runtime_state.active_run_ids
                    if self._run_tag(instance, run_id, RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
                )
                partitions.append(
                    PartitionIncidentEvidence(
                        partition_key=key,
                        freshness_overdue=overdue,
                        exact_partition_complete=runtime_state.observation.materialized,
                        missing_mart_asset_keys=runtime_state.missing_mart_asset_keys,
                        run_ids=runtime_state.run_ids,
                        failed_run_ids=runtime_state.failed_run_ids,
                        successful_run_ids=runtime_state.successful_run_ids,
                        latest_failed_run=latest_failed,
                        recovery=RecoveryPolicySnapshot(
                            action=decision.action.value,
                            reason_code=decision.reason_code,
                            explanation=decision.explanation,
                            observed_auto_replay_attempts=runtime_state.observation.auto_replay_attempts,
                            active_run_ids=runtime_state.active_run_ids,
                            active_recovery_run_ids=active_recovery_ids,
                        ),
                        infrastructure_healthy=infrastructure_healthy,
                    )
                )
                current += timedelta(days=1)
        except Exception as exc:
            return self._deferred(f"Dagster incident-state read failed: {exc}")

        incomplete = tuple(item for item in partitions if not item.exact_partition_complete)
        if not incomplete:
            return IncidentDrilldownResult(
                status=IncidentDrilldownStatus.NO_INCIDENT,
                evidence="RUNTIME_VERIFIED",
                partitions=tuple(partitions),
                validation="ALL_QUERIED_PARTITIONS_COMPLETE",
            )

        if any(item.latest_failed_run is None for item in incomplete):
            warnings.append(
                "At least one incomplete partition has no structured failed-run evidence; do not invent a failure cause from absence of a run."
            )

        return IncidentDrilldownResult(
            status=IncidentDrilldownStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            partitions=tuple(partitions),
            warnings=warnings,
            validation="STRUCTURED_INCIDENT_EVIDENCE_COLLECTED",
        )

    @staticmethod
    def _latest_failed_run_evidence(instance, run_id, *, source_tag: str, component_tag: str, reason_tag: str, stage_tag: str):
        if not run_id:
            return None
        run = instance.get_run_by_id(run_id)
        if run is None:
            return None
        status = getattr(run.status, "value", str(run.status))
        tags = dict(getattr(run, "tags", {}) or {})
        failure_class = tags.get("commerce/failure_class", "unknown")
        return FailedRunEvidence(
            run_id=run_id,
            status=status,
            failure_class=failure_class,
            failure_source=tags.get(source_tag),
            failure_component=tags.get(component_tag),
            failure_reason=tags.get(reason_tag),
            failure_stage=tags.get(stage_tag),
        )

    @staticmethod
    def _run_tag(instance, run_id: str, tag: str) -> str | None:
        run = instance.get_run_by_id(run_id)
        if run is None:
            return None
        return (getattr(run, "tags", {}) or {}).get(tag)

    @staticmethod
    def _parse(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

    @staticmethod
    def _deferred(detail: str) -> IncidentDrilldownResult:
        return IncidentDrilldownResult(
            status=IncidentDrilldownStatus.DEFERRED,
            evidence="DEFERRED",
            warnings=[detail],
            validation="RUNTIME_EVIDENCE_UNAVAILABLE",
        )
