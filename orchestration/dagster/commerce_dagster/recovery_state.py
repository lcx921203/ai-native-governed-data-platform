"""Read exact-partition materialization/run facts for bounded Phase 3C recovery."""

from __future__ import annotations

from dataclasses import dataclass

import dagster as dg

from .automation_policy import (
    SHOPIFY_DAILY_JOB_NAME,
    SHOPIFY_DAILY_MART_ASSET_KEYS,
    SHOPIFY_DAILY_PARTITION_TAG,
)
from .failure_classification import FAILURE_CLASS_TAG, FailureClass
from .recovery_policy import RecoveryObservation


RECOVERY_TAG = "commerce/recovery"
RECOVERY_ATTEMPT_TAG = "commerce/recovery_attempt"
RECOVERY_REASON_TAG = "commerce/recovery_reason"
AUTO_RECOVERY_TAG_VALUE = "auto"

ACTIVE_RUN_STATUSES = frozenset(
    {
        dg.DagsterRunStatus.NOT_STARTED,
        dg.DagsterRunStatus.QUEUED,
        dg.DagsterRunStatus.STARTING,
        dg.DagsterRunStatus.STARTED,
        dg.DagsterRunStatus.MANAGED,
        dg.DagsterRunStatus.CANCELING,
    }
)
FAILED_RUN_STATUSES = frozenset(
    {dg.DagsterRunStatus.FAILURE, dg.DagsterRunStatus.CANCELED}
)


@dataclass(frozen=True)
class RecoveryRuntimeState:
    observation: RecoveryObservation
    missing_mart_asset_keys: tuple[str, ...]
    run_ids: tuple[str, ...]
    active_run_ids: tuple[str, ...]
    failed_run_ids: tuple[str, ...]
    successful_run_ids: tuple[str, ...]
    latest_failed_run_id: str | None


def _asset_partition_materialized(
    instance: dg.DagsterInstance,
    asset_key: str,
    partition_key: str,
) -> bool:
    result = instance.fetch_materializations(
        dg.AssetRecordsFilter(
            asset_key=dg.AssetKey([asset_key]),
            asset_partitions=[partition_key],
        ),
        limit=1,
    )
    return bool(result.records)


def _failure_class_from_run(run: dg.DagsterRun | None) -> FailureClass:
    if run is None:
        return FailureClass.NONE
    raw_value = run.tags.get(FAILURE_CLASS_TAG)
    if raw_value is None:
        return FailureClass.UNKNOWN
    try:
        return FailureClass(raw_value)
    except ValueError:
        return FailureClass.UNKNOWN


def collect_partition_recovery_state(
    instance: dg.DagsterInstance,
    *,
    partition_key: str,
    freshness_overdue: bool,
    infrastructure_healthy: bool,
    missed_schedule_eligible: bool = False,
) -> RecoveryRuntimeState:
    missing_marts = tuple(
        k
        for k in SHOPIFY_DAILY_MART_ASSET_KEYS
        if not _asset_partition_materialized(instance, k, partition_key)
    )
    run_records = instance.get_run_records(
        filters=dg.RunsFilter(
            job_name=SHOPIFY_DAILY_JOB_NAME,
            tags={SHOPIFY_DAILY_PARTITION_TAG: partition_key},
        ),
        order_by="id",
        ascending=False,
    )
    runs = tuple(record.dagster_run for record in run_records)
    active_runs = tuple(run for run in runs if run.status in ACTIVE_RUN_STATUSES)
    failed_runs = tuple(run for run in runs if run.status in FAILED_RUN_STATUSES)
    successful_runs = tuple(
        run for run in runs if run.status is dg.DagsterRunStatus.SUCCESS
    )
    auto_replay_attempts = sum(
        1 for run in runs if run.tags.get(RECOVERY_TAG) == AUTO_RECOVERY_TAG_VALUE
    )
    latest_failed_run = failed_runs[0] if failed_runs else None

    observation = RecoveryObservation(
        partition_key=partition_key,
        freshness_overdue=freshness_overdue,
        materialized=not missing_marts,
        active_run=bool(active_runs),
        failed_run=bool(failed_runs),
        successful_run=bool(successful_runs),
        failure_class=_failure_class_from_run(latest_failed_run),
        infrastructure_healthy=infrastructure_healthy,
        auto_replay_attempts=auto_replay_attempts,
        missed_schedule_eligible=missed_schedule_eligible,
    )
    return RecoveryRuntimeState(
        observation=observation,
        missing_mart_asset_keys=missing_marts,
        run_ids=tuple(run.run_id for run in runs),
        active_run_ids=tuple(run.run_id for run in active_runs),
        failed_run_ids=tuple(run.run_id for run in failed_runs),
        successful_run_ids=tuple(run.run_id for run in successful_runs),
        latest_failed_run_id=latest_failed_run.run_id if latest_failed_run else None,
    )
