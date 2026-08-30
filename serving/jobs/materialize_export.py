"""把 MetricFlow CSV 原子替换为 Iceberg Serving 日分区。

业务逻辑：MetricFlow 先决定指标数值，本作业只做列映射、显式类型转换和 Iceberg 物理写入。
输入：Serving Contract + MetricFlow CSV + Dagster partition_key。
输出：``polaris.serving.*`` Iceberg Table 的对应 business_date 分区。
Spark / Iceberg API：``DataFrameWriterV2.overwrite(filter)`` 精确替换本次业务日分区，保持重跑幂等并支持空结果清理。
工程边界：本作业不重新计算指标；CSV 缺列、主键为空、业务日错位都会在写入前失败。
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pyspark.sql import SparkSession, functions as F

from serving.contracts import load_serving_contract


def parse_args() -> argparse.Namespace:
    """解析 Dagster/Spark 传入的 Contract、CSV Artifact 与业务日分区。

    三个参数都必须显式给出；Writer 不自行猜测当前日期或目标表。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, help="Project-relative Serving YAML path")
    parser.add_argument("--csv", required=True, help="Project-relative MetricFlow CSV artifact")
    parser.add_argument("--partition-key", required=True, help="Dagster YYYY-MM-DD business partition")
    return parser.parse_args()


def _sql_columns(contract) -> str:
    """根据 Contract 生成 Iceberg DDL 列定义；列类型来自显式白名单，不接受任意 SQL。"""

    columns = [f"{column.name} {column.spark_type}" for column in contract.target.columns]
    columns.extend(["serving_contract STRING", "materialized_at TIMESTAMP"])
    return ",\n  ".join(columns)


def main() -> None:
    """验证 MetricFlow 结果并原子替换一个 Iceberg Serving 日分区。

    校验顺序：Header → 显式类型映射 → Primary Key 非空 → Grain 唯一 → Business Date 对齐 → 写表。
    任何一步失败都发生在 overwrite 之前，避免把不完整结果发布给 BI/API。
    """
    args = parse_args()
    partition_day = date.fromisoformat(args.partition_key)
    project_root = PROJECT_ROOT
    contract = load_serving_contract(project_root / args.contract)
    csv_path = project_root / args.csv

    spark = SparkSession.builder.appName(f"serving-{contract.name}").getOrCreate()
    source = spark.read.option("header", True).csv(str(csv_path))

    missing = sorted(set(contract.expected_metricflow_columns) - set(source.columns))
    if missing:
        raise ValueError(f"MetricFlow CSV missing expected columns before materialization: {missing}")

    expressions = [
        F.col(column.source).cast(column.spark_type).alias(column.name)
        for column in contract.target.columns
    ]
    prepared = (
        source.select(*expressions)
        .withColumn("serving_contract", F.lit(contract.name))
        .withColumn("materialized_at", F.current_timestamp())
    )

    for key in contract.target.primary_key:
        if prepared.where(F.col(key).isNull()).limit(1).count() > 0:
            raise ValueError(f"Serving primary-key column contains NULL: {key}")

    duplicate_key = (
        prepared.groupBy(*contract.target.primary_key)
        .count()
        .where(F.col("count") > 1)
        .limit(1)
        .count()
    )
    if duplicate_key:
        raise ValueError(
            "Serving export violates target grain uniqueness: "
            + ",".join(contract.target.primary_key)
        )

    partition_column = contract.target.partition_by[0]
    wrong_day = prepared.where(F.col(partition_column) != F.lit(partition_day)).limit(1).count()
    if wrong_day:
        raise ValueError(
            f"Serving export contains rows outside Dagster partition {partition_day.isoformat()}"
        )

    catalog, schema, _ = contract.target.table.split(".", 2)
    spark.sql(f"CREATE NAMESPACE IF NOT EXISTS {catalog}.{schema}")

    partition_clause = ", ".join(contract.target.partition_by)
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {contract.target.table} (
          {_sql_columns(contract)}
        )
        USING iceberg
        PARTITIONED BY ({partition_clause})
        """
    )

    # Iceberg WriterV2 的 overwrite(filter) 用一个 Snapshot 精确替换当前业务日分区。
    # 与 dynamic overwrite 不同，即使 MetricFlow 本次返回 0 行，这个 filter 仍能清掉旧分区，
    # 避免“今天已无数据但 Dashboard 还保留上次旧值”的陈旧结果。
    prepared.writeTo(contract.target.table).overwrite(
        F.col(partition_column) == F.lit(partition_day)
    )

    count = prepared.count()
    print(
        f"serving materialization complete: contract={contract.name} "
        f"table={contract.target.table} partition={partition_day} rows={count}"
    )


if __name__ == "__main__":
    main()
