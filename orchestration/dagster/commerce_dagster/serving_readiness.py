"""Serving Export 的 exact-partition Readiness Gate。

业务逻辑：固定 Dashboard/API 只有在 Serving Contract 声明的上游日分区 Asset 都已经出现
Dagster Materialization Event 后，才允许执行 MetricFlow Export。
输入：Dagster Instance + partition_key + required asset keys；输出：缺失 Asset tuple。
工程边界：这里只证明编排层 Materialization Evidence，不把它升级成 Iceberg 行级/业务完整性证明；
真正的 MetricFlow 查询仍可能因数据或语义问题失败，并必须 Fail Closed。
"""

from __future__ import annotations

import dagster as dg


def missing_daily_asset_partitions(
    instance: dg.DagsterInstance,
    *,
    partition_key: str,
    required_asset_keys: tuple[str, ...],
) -> tuple[str, ...]:
    """返回本次 Serving Export 尚未物化的 exact daily Asset。"""

    missing: list[str] = []
    for asset_key in required_asset_keys:
        result = instance.fetch_materializations(
            dg.AssetRecordsFilter(
                asset_key=dg.AssetKey([asset_key]),
                asset_partitions=[partition_key],
            ),
            limit=1,
        )
        if not result.records:
            missing.append(asset_key)
    return tuple(missing)
