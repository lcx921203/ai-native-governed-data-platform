"""当前九张受治理 Mart 的 exact-partition Recovery State Reader（精确分区恢复状态读取器）。

为什么这里保留一个独立的 current-layer reader：
- 历史 Phase 6 ZIP / SHA-256 继续作为当时的八表闭包证据；
- 当前 canonical source 可以继续演进，不受历史包“只读”限制；
- 这里选择独立 reader，是为了让“Phase 6 的原八表语义”和“当前九表 SLA”在源码结构上都可追溯，而不是因为 current source 不能修改。

这里仍然只读取 Dagster Event Storage / Run Storage 的运行事实，不执行恢复动作，
也不把 Materialization Event 冒充成 Iceberg 行级完整性证明。
"""

from __future__ import annotations

from dataclasses import dataclass

import dagster as dg

from .automation_policy import (
    SHOPIFY_DAILY_JOB_NAME,
    SHOPIFY_DAILY_PARTITION_TAG,
)
from .consumer_sla import SHOPIFY_DAILY_MART_ASSET_KEYS
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
    """一次 exact partition 的完整 Recovery 输入快照。

    ``tuple[str, ...]`` 里的 ``...`` 是 Python 真实类型语法，不是博客省略号；
    它表示“这个 tuple 可以包含任意多个 str”。
    """

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
    """检查某一 Mart 的精确分区是否至少出现过一次 Materialization Event。

    输入是 Dagster Instance、Asset Key 和 exact partition key；输出 bool。
    这里证明的是 Dagster Event Storage 里的编排事实，不证明 Iceberg 表内行数或业务口径完整。
    """

    result = instance.fetch_materializations(
        dg.AssetRecordsFilter(
            asset_key=dg.AssetKey([asset_key]),
            asset_partitions=[partition_key],
        ),
        limit=1,
    )
    return bool(result.records)


def _failure_class_from_run(run: dg.DagsterRun | None) -> FailureClass:
    """从一个历史 Run 的结构化 Tag 解析 FailureClass。

    没有 Run 时返回 ``NONE``；Tag 缺失或值非法时返回 ``UNKNOWN``。
    Recovery 不会通过自由文本日志猜测一个更宽松、可自动重放的失败类别。
    """

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
    """收集 Recovery Policy 所需的当前 exact-partition 事实，不在这里执行恢复。

    核心逻辑：
    1. 逐一检查九张受治理消费者 Mart（现在包含 ``order_lifecycle_snapshot``）；
    2. 读取同一个 daily job + exact partition 的全部 Run history；
    3. 分离 Active / Failed / SUCCESS Run；
    4. 统计历史自动 Replay 次数，并读取最新失败类；
    5. 生成不可变 ``RecoveryRuntimeState`` 给纯 ``decide_recovery`` 使用。

    输出中的 ``materialized=True`` 只表示九张 Mart 都有该 exact partition 的 Dagster
    Materialization Event；真实 Iceberg 数据完整性仍需 Runtime/Data Evidence 单独证明。
    """

    missing_marts = tuple(
        key
        for key in SHOPIFY_DAILY_MART_ASSET_KEYS
        if not _asset_partition_materialized(instance, key, partition_key)
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
