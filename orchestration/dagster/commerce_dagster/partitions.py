"""Phase 3B/3C 的 Logical Partition 与 Effective Source Window 契约。

Dagster 负责定义稳定的业务日逻辑窗口；Source Reader 只把读取开始边界向前扩 5 分钟
作为 Lookback 防漏，逻辑分区身份本身不改变。dbt 接收自己的窗口语义，不能把 Source
Lookback 误当成业务建模分区。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import dagster as dg


SHOPIFY_DAILY_PARTITIONS = dg.DailyPartitionsDefinition(
    start_date="2026-08-01",
    timezone="UTC",
)
SHOPIFY_SOURCE_LOOKBACK_MINUTES = 5


def _iso_utc(value) -> str:
    """把带时区的 datetime 转成 Spark/dbt 接受的 UTC ISO-8601 字符串。
    
    输入必须带时区；输出统一为 ``...Z``。如果传入 naive datetime 就直接失败，避免本地时间被误当成源读取窗口。
    """
    return value.isoformat().replace("+00:00", "Z")


def shopify_logical_window(
    context: dg.AssetExecutionContext | dg.AssetCheckExecutionContext,
):
    """根据 Dagster 日分区键计算逻辑业务窗口。
    
    输入 ``YYYY-MM-DD`` 分区键；输出严格的 UTC 半开区间 ``[start, end)``。这个窗口代表要负责完成的源更新时间日，不包含 Lookback。
    """

    return context.partition_time_window


def shopify_effective_window(
    context: dg.AssetExecutionContext | dg.AssetCheckExecutionContext,
):
    """在逻辑分区窗口基础上加入源端 Lookback。
    
    开始时间向前扩一小段以优先避免漏数，结束时间保持不变；重复 Observation 由后续幂等版本逻辑收敛。
    """

    logical = shopify_logical_window(context)
    return (
        logical.start - timedelta(minutes=SHOPIFY_SOURCE_LOOKBACK_MINUTES),
        logical.end,
    )


def shopify_source_window_args(
    context: dg.AssetExecutionContext | dg.AssetCheckExecutionContext,
) -> list[str]:
    """把有效读取窗口转换成传给 Shopify/Spark 采集脚本的命令行参数。
    
    输出是稳定的字符串参数列表；只传源读取边界，不改变 Dagster 逻辑分区身份。
    """

    effective_start, effective_end = shopify_effective_window(context)
    return [
        "--window-start",
        _iso_utc(effective_start),
        "--window-end",
        _iso_utc(effective_end),
    ]


def shopify_dbt_window_vars(context: dg.AssetExecutionContext) -> dict[str, Any]:
    """把逻辑分区窗口转换成 dbt ``--vars`` 需要的变量字典。
    
    dbt 使用逻辑窗口发现本分区发生变化的业务键，不继承 API Lookback，避免把源端防漏策略扩散成建模语义。
    """

    effective_start, effective_end = shopify_effective_window(context)
    return {
        "shopify_effective_start": _iso_utc(effective_start),
        "shopify_effective_end": _iso_utc(effective_end),
    }
