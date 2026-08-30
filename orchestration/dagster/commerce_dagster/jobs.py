"""Phase 3C 的 Dagster 可执行 Job 边界。

这里把 Foundation 与 Daily Partition 两类 Asset Selection 分开，并统一挂有界 Step Retry。
Job 只描述一次 Run 应该物化哪些 Asset；Cross-run Recovery 由独立 Sensor / Policy 决定。
"""

import dagster as dg

from .automation_policy import SHOPIFY_DAILY_JOB_NAME
from .assets.dbt import (
    commerce_dbt_assets,
    commerce_staging_dbt_assets,
    commerce_windowed_dbt_assets,
)
from .assets.lakehouse import raw_shopify_order_payload, shopify_structured_source
from .assets.serving import bi_daily_executive


# Dagster API：RetryPolicy 只作用于一次 Run 内的 Step Retry；它不是 Cross-run Recovery。
# max_retries=2 + Exponential Backoff + Full Jitter 用于已经证明可安全重试的瞬时执行失败。
TRANSIENT_RETRY_POLICY = dg.RetryPolicy(
    max_retries=2,
    delay=30,
    backoff=dg.Backoff.EXPONENTIAL,
    jitter=dg.Jitter.FULL,
)

# Foundation Job：负责 seeds / master data / time spine 与全局 Staging View，通常由部署或人工触发。
# Dagster API：define_asset_job 用 AssetSelection 声明 Job 边界，不在这里直接写执行顺序。
commerce_dbt_foundation_job = dg.define_asset_job(
    name="commerce_dbt_foundation_job",
    selection=dg.AssetSelection.assets(
        commerce_dbt_assets,
        commerce_staging_dbt_assets,
    ),
    op_retry_policy=TRANSIENT_RETRY_POLICY,
    description=(
        "Deployment job for seeds/master data/time spine and global Staging Views."
    ),
    run_tags={
        "commerce/execution_class": "foundation",
        "commerce/automation": "manual-or-deploy",
    },
)

# Daily Partition Job：一个 completed Shopify business day 的 Raw → Structured Source → window-aware dbt 链路。
# 工程边界：Job SUCCESS 只说明 Run 没失败；Recovery 仍必须检查九张 Consumer Mart 的 exact partition 完整性。
shopify_daily_partition_job = dg.define_asset_job(
    name=SHOPIFY_DAILY_JOB_NAME,
    selection=dg.AssetSelection.assets(
        raw_shopify_order_payload,
        shopify_structured_source,
        commerce_windowed_dbt_assets,
    ),
    op_retry_policy=TRANSIENT_RETRY_POLICY,
    description=(
        "One completed Shopify source-update day: Raw -> Structured Source -> "
        "window-aware Current State/Marts."
    ),
    run_tags={
        "commerce/execution_class": "daily-partition",
        "commerce/partition_clock": "shopify-order-updated-at",
        "commerce/replay_semantics": "idempotent-or-at-least-once-safe",
    },
)


# Serving Export Job：固定 BI/API 消费独立于 Raw/dbt 正常调度，避免“上游 Run 成功”与“消费投影完成”混为一谈。
serving_daily_export_job = dg.define_asset_job(
    name="serving_daily_export_job",
    selection=dg.AssetSelection.assets(bi_daily_executive),
    op_retry_policy=TRANSIENT_RETRY_POLICY,
    description=(
        "Materialize one completed business day of governed MetricFlow results into Iceberg Serving."
    ),
    run_tags={
        "commerce/execution_class": "serving-export",
        "commerce/metric_authority": "metricflow",
        "commerce/consumer_surface": "bi-api",
    },
)
