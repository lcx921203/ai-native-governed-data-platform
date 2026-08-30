"""Phase 3C 基于业务日分区的 Dagster Schedule 定义。

Dagster API：``build_schedule_from_partitioned_job`` 根据分区 Job 生成固定时刻的 Schedule。
当前默认保持 STOPPED；只有真实 Daemon Runtime Acceptance 完成后，才能把“定义存在”升级为已启用。
"""

import dagster as dg

from .automation_policy import (
    SHOPIFY_DAILY_SCHEDULE_HOUR,
    SHOPIFY_DAILY_SCHEDULE_MINUTE,
    SERVING_DAILY_EXPORT_SCHEDULE_HOUR,
    SERVING_DAILY_EXPORT_SCHEDULE_MINUTE,
)
from .jobs import serving_daily_export_job, shopify_daily_partition_job


shopify_daily_partition_schedule = dg.build_schedule_from_partitioned_job(
    shopify_daily_partition_job,
    name="shopify_daily_partition_schedule",
    hour_of_day=SHOPIFY_DAILY_SCHEDULE_HOUR,
    minute_of_hour=SHOPIFY_DAILY_SCHEDULE_MINUTE,
    # 工程边界：真实 Dagster Daemon Runtime Acceptance 尚未完成前，Schedule 默认 STOPPED，保持 Fail Closed。
    default_status=dg.DefaultScheduleStatus.STOPPED,
    tags={
        "commerce/automation": "daily-schedule",
        "commerce/schedule_contract": "latest-completed-daily-partition",
    },
    description=(
        "At 00:15 UTC, materialize the most recent completed Shopify daily partition."
    ),
)


# 固定 Dashboard/API 在主日链之后独立导出；00:45 是运行计划，不等同于消费 Freshness SLA。
serving_daily_export_schedule = dg.build_schedule_from_partitioned_job(
    serving_daily_export_job,
    name="serving_daily_export_schedule",
    hour_of_day=SERVING_DAILY_EXPORT_SCHEDULE_HOUR,
    minute_of_hour=SERVING_DAILY_EXPORT_SCHEDULE_MINUTE,
    default_status=dg.DefaultScheduleStatus.STOPPED,
    tags={
        "commerce/automation": "serving-daily-export",
        "commerce/metric_authority": "metricflow",
    },
    description=(
        "At 00:45 UTC, export the most recent completed business day into the BI/API Serving table."
    ),
)
