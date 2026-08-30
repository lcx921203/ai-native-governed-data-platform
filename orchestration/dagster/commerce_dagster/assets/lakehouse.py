"""Shopify -> Raw -> Structured Source 的 Dagster Asset 定义。"""

from __future__ import annotations

import json

import dagster as dg
from dagster_dbt import get_asset_keys_by_output_name_for_source

from ..partitions import (
    SHOPIFY_DAILY_PARTITIONS,
    shopify_effective_window,
    shopify_logical_window,
    shopify_source_window_args,
)
from ..project import PROJECT_ROOT
from ..resources import SparkComposeResource

from ingestion.shopify.extract_orders import extract_orders_in_window
from ingestion.shopify.source_config import load_source_config
from .dbt import commerce_dbt_assets, commerce_staging_dbt_assets, commerce_windowed_dbt_assets


RAW_ASSET_KEY = dg.AssetKey(["raw", "shopify_order_payload"])

SHOPIFY_SOURCE_ASSET_KEYS = get_asset_keys_by_output_name_for_source(
    [commerce_staging_dbt_assets, commerce_dbt_assets, commerce_windowed_dbt_assets],
    "shopify",
)


@dg.asset(
    key=RAW_ASSET_KEY,
    partitions_def=SHOPIFY_DAILY_PARTITIONS,
    backfill_policy=dg.BackfillPolicy.multi_run(),
    group_name="ingestion",
    kinds={"shopify", "spark", "iceberg"},
    description="Append Shopify observations for one Dagster-owned source-update day.",
    metadata={
        "physical_table": "polaris.raw.raw_shopify_order_payload",
        "grain": "one API observation",
        "write_semantics": "append-only / at-least-once",
    },
)
def raw_shopify_order_payload(
    context: dg.AssetExecutionContext,
    spark: SparkComposeResource,
) -> dg.MaterializeResult:
    """把一个 Dagster 分区对应的 Shopify Observation 物化到 Raw。

    ``SHOPIFY_SOURCE_MODE`` 只决定“Observation 从哪里来”：
    - fixture：本地模拟数据；
    - production：真实 Shopify Admin GraphQL API。

    两条路都写同一 Raw Schema，并保持 append-only / at-least-once，
    所以下游 Normalize / dbt 不需要复制成两套代码。
    """

    # logical：Dagster 真正拥有的逻辑日分区。
    # effective：真正读源的窗口，开始边界可以因为 Lookback 向前扩展。
    logical = shopify_logical_window(context)
    effective_start, effective_end = shopify_effective_window(context)

    # 数据源选择来自运行环境，不需要额外配置文件。
    source = load_source_config()

    if source.kind == "fixture":
        # Fixture 路径：Spark 直接读取本地 JSON 模拟数据并 append Raw。
        spark.spark_submit(
            "ingestion/shopify/load_fixtures.py",
            context,
            script_args=shopify_source_window_args(context),
        )
        observation_count = None
        landing_file = None

    else:
        # Production 路径分两步：
        # 1) Dagster Python 进程访问 Shopify HTTPS，完成所有分页；
        # 2) 把 JSONL Landing 交给 Spark 写 Raw Iceberg。
        orders = extract_orders_in_window(
            effective_start,
            effective_end,
            page_size=int(source.get("page_size", 100)),
            nested_page_size=int(source.get("nested_page_size", 100)),
            api_version=str(source.get("api_version", "2026-07")),
            timeout_seconds=int(source.get("timeout_seconds", 60)),
            max_retries=int(source.get("max_retries", 5)),
            backoff_base_seconds=float(source.get("backoff_base_seconds", 1.0)),
            throttle_reserve_points=float(source.get("throttle_reserve_points", 20.0)),
            enforce_api_version=bool(source.get("enforce_api_version", True)),
            nested_pagination=bool(source.get("nested_pagination", True)),
        )

        # Landing 文件只属于本次运行的中间交接 / 运行证据，放在 .runtime 下，
        # 不把它当成源码，也不把它当成新的业务真值层。
        landing_dir = PROJECT_ROOT / str(source.get("landing_dir", ".runtime/shopify-api"))
        landing_dir.mkdir(parents=True, exist_ok=True)
        landing_path = landing_dir / f"orders-{context.partition_key}.jsonl"

        # with：文件写完自动关闭；ensure_ascii=False 保持 Unicode 可读。
        with landing_path.open("w", encoding="utf-8") as handle:
            for order in orders:
                handle.write(json.dumps(order, ensure_ascii=False) + "\n")

        relative_landing = landing_path.relative_to(PROJECT_ROOT)
        spark.spark_submit(
            "ingestion/shopify/load_api_observations.py",
            context,
            script_args=["--input-file", str(relative_landing)],
        )
        observation_count = len(orders)
        landing_file = str(relative_landing)

    return dg.MaterializeResult(
        metadata={
            "partition_key": context.partition_key,
            "logical_window_start": logical.start.isoformat(),
            "logical_window_end": logical.end.isoformat(),
            "effective_source_start": effective_start.isoformat(),
            "effective_source_end": effective_end.isoformat(),
            "source_mode": source.mode,
            "source_kind": source.kind,
            "observation_count": observation_count,
            "landing_file": landing_file,
            "contract": "at-least-once; duplicate observations are allowed",
        }
    )


@dg.multi_asset(
    name="normalize_shopify_order_domain",
    outs={
        output_name: dg.AssetOut(key=asset_key)
        for output_name, asset_key in SHOPIFY_SOURCE_ASSET_KEYS.items()
    },
    deps=[RAW_ASSET_KEY],
    partitions_def=SHOPIFY_DAILY_PARTITIONS,
    backfill_policy=dg.BackfillPolicy.multi_run(),
    group_name="structured_source",
    can_subset=False,
    description=(
        "For one source-update partition, explode Shopify nested collections and MERGE "
        "business-key + record-hash versions into Structured Source Iceberg tables."
    ),
)
def shopify_structured_source(
    context: dg.AssetExecutionContext,
    spark: SparkComposeResource,
):
    """把 Raw Observation 标准化为 Structured Source 业务版本。"""

    spark.spark_submit(
        "lakehouse/jobs/normalize_shopify_orders.py",
        context,
        script_args=shopify_source_window_args(context),
    )

    # yield：生成器语法。这里会依次向 Dagster 产出多个 Output，而不是一次 return 一个值。
    for output_name, asset_key in SHOPIFY_SOURCE_ASSET_KEYS.items():
        yield dg.Output(
            value=None,
            output_name=output_name,
            metadata={
                "partition_key": context.partition_key,
                "asset_key": asset_key.to_user_string(),
                "grain_contract": "business key x distinct record_hash",
                "write_semantics": "idempotent MERGE",
            },
        )
