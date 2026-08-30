"""固定 BI/API Serving Projection 的 Dagster Asset。

业务逻辑：一个 Dagster daily partition 先由 MetricFlow 生成受治理指标结果，再调用 Spark 把该结果物化到 Iceberg Serving。
输入：已完成的 dbt Marts + Serving Contract；输出：``serving/bi_daily_executive`` Asset Materialization。
Dagster API：Asset dependency 只表达数据依赖；固定导出拥有独立 Schedule，不改变上游 Raw/dbt Run 的所有权。
工程边界：Asset 不重写指标公式；MetricFlow 未开放/失败时禁止写 Serving Table。
"""

from __future__ import annotations

import dagster as dg

from serving.contracts import load_serving_contract
from serving.exporter import ExportStatus, MetricFlowServingExporter

from ..partitions import SHOPIFY_DAILY_PARTITIONS
from ..project import PROJECT_ROOT
from ..resources import SparkComposeResource
from ..serving_readiness import missing_daily_asset_partitions


SERVING_CONTRACT_PATH = PROJECT_ROOT / "serving" / "contracts" / "bi_daily_executive.yml"


@dg.asset(
    name="bi_daily_executive",
    key_prefix=["serving"],
    group_name="serving",
    partitions_def=SHOPIFY_DAILY_PARTITIONS,
    deps=[
        dg.AssetKey(["orders"]),
        dg.AssetKey(["order_items"]),
        dg.AssetKey(["refund_items"]),
        dg.AssetKey(["stores"]),
    ],
    description=(
        "Fixed MetricFlow executive query materialized as an Iceberg Serving table for BI/API consumers."
    ),
)
def bi_daily_executive(
    context: dg.AssetExecutionContext,
    spark: SparkComposeResource,
) -> dg.MaterializeResult:
    """物化一个 business_date × region 的固定 Serving 分区。

    MetricFlow 先产出 CSV，成功且结构校验通过后才进入 Spark；Spark WriterV2 负责对目标 Iceberg 日分区做幂等替换。
    """

    contract = load_serving_contract(SERVING_CONTRACT_PATH)

    missing_upstream = missing_daily_asset_partitions(
        context.instance,
        partition_key=context.partition_key,
        required_asset_keys=contract.readiness.required_daily_assets,
    )
    if missing_upstream:
        raise dg.Failure(
            description=(
                "Serving export refused because required exact-partition upstream assets "
                f"are incomplete: {', '.join(missing_upstream)}"
            ),
            metadata={
                "serving_contract": contract.name,
                "partition_key": context.partition_key,
                "missing_upstream_assets": ",".join(missing_upstream),
                "readiness": "INCOMPLETE",
            },
            allow_retries=False,
        )

    exporter = MetricFlowServingExporter(PROJECT_ROOT)
    result = exporter.export(contract, context.partition_key)

    if result.status is not ExportStatus.COMPLETE or result.csv_path is None:
        raise dg.Failure(
            description=(
                f"Serving MetricFlow export did not complete: status={result.status.value}; "
                f"reason={result.message}"
            ),
            metadata={
                "serving_contract": contract.name,
                "partition_key": context.partition_key,
                "export_status": result.status.value,
            },
            allow_retries=False,
        )

    csv_relative = result.csv_path.relative_to(PROJECT_ROOT)
    contract_relative = SERVING_CONTRACT_PATH.relative_to(PROJECT_ROOT)
    spark.spark_submit(
        "serving/jobs/materialize_export.py",
        context,
        script_args=(
            "--contract",
            str(contract_relative),
            "--csv",
            str(csv_relative),
            "--partition-key",
            context.partition_key,
        ),
    )

    return dg.MaterializeResult(
        metadata={
            "serving_contract": contract.name,
            "target_table": contract.target.table,
            "partition_key": context.partition_key,
            "consumers": ",".join(contract.consumers),
            "metric_names": ",".join(contract.semantic_query.metrics),
            "metric_authority": "MetricFlow",
        }
    )
