"""A → B → A 业务状态回退的集成验收程序。

核心不变量：再次观察到与历史版本相同的业务内容时，只能扩展该内容版本的
Observation / Source Update 时间范围，不能创建第三个内容版本。
因此 Structured Source 最终只保留 A / B 两个 distinct Business Version，
而 Current State 必须重新选择回 A。

工程边界：这是可执行 Acceptance Program；没有真实 Spark / Iceberg 运行日志时，
只能标记为 SOURCE DEFINED / NOT EXECUTED，不能包装成 Runtime PASS。
"""
from datetime import datetime

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from normalize_shopify_orders import add_record_hash, merge_source_versions

TARGET = "polaris.runtime_acceptance.business_version_rollback"


def observation_df(spark, state: str, source_updated_at: str, extracted_at: str, batch_id: str):
    """构造一条最小 Order Observation DataFrame，用于 A→B→A 集成验收。
    
    输入：业务状态、source_updated_at、extracted_at、batch_id。
    输出：符合 Normalize 输入契约的 Spark DataFrame。
    工程目的：控制唯一变化字段，精确验证 record_hash 去重与 Current State 回滚语义。
    """
    df = spark.createDataFrame(
        [("order-rollback-demo", state, source_updated_at, extracted_at, batch_id)],
        "object_id string, state string, source_updated_at string, extracted_at string, batch_id string",
    ).select(
        "object_id",
        "state",
        F.to_timestamp("source_updated_at").alias("source_updated_at"),
        F.to_timestamp("extracted_at").alias("extracted_at"),
        "batch_id",
    )
    return add_record_hash(df, ["object_id", "state"])


def assert_eq(label, actual, expected):
    """执行带业务标签的相等断言。
    
    输入：label、actual、expected。
    输出：相等时无返回；不等时抛 AssertionError。
    工程目的：让集成验收失败信息直接指出哪个业务断言被破坏。
    """
    if actual != expected:
        raise AssertionError(f"{label}: expected={expected!r}, actual={actual!r}")
    print(f"PASS {label}: {actual!r}")


def main() -> None:
    """执行 A→B→A Business Version Rollback 验收场景。
    
    步骤：写入 A、写入 B、再次写入 A；检查 Structured Source 只保留 A/B 两个 distinct content versions，并检查 Current State 最终回到 A。
    工程边界：这是可执行 Acceptance Program；没有真实 Spark/Iceberg 日志时状态仍是 NOT EXECUTED，不能写成 Runtime PASS。
    """
    spark = SparkSession.builder.appName("business-version-rollback-acceptance").getOrCreate()
    spark.sql("CREATE NAMESPACE IF NOT EXISTS polaris.runtime_acceptance")
    spark.sql(f"DROP TABLE IF EXISTS {TARGET}")
    spark.sql(f"""
        CREATE TABLE {TARGET} (
            object_id STRING,
            state STRING,
            source_updated_at TIMESTAMP,
            extracted_at TIMESTAMP,
            batch_id STRING,
            record_hash STRING,
            first_observed_at TIMESTAMP,
            last_observed_at TIMESTAMP,
            first_source_updated_at TIMESTAMP,
            last_source_updated_at TIMESTAMP
        ) USING iceberg
    """)

    try:
        steps = [
            ("OPEN", "2026-08-01 10:00:00", "2026-08-01 10:00:10", "batch-a"),
            ("CLOSED", "2026-08-01 11:00:00", "2026-08-01 11:00:10", "batch-b"),
            ("OPEN", "2026-08-01 12:00:00", "2026-08-01 12:00:10", "batch-c"),
        ]

        for index, (state, source_time, observed_time, batch_id) in enumerate(steps, start=1):
            merge_source_versions(
                spark=spark,
                df=observation_df(spark, state, source_time, observed_time, batch_id),
                target_table=TARGET,
                temp_view=f"rollback_step_{index}",
                business_keys=["object_id"],
                source_updated_column="source_updated_at",
            )

        rows = spark.table(TARGET)
        assert_eq("distinct content versions", rows.count(), 2)

        duplicate_count = (
            rows.groupBy("object_id", "record_hash")
            .count()
            .filter(F.col("count") > 1)
            .count()
        )
        assert_eq("business key + record_hash duplicates", duplicate_count, 0)

        current = (
            rows.orderBy(
                F.col("last_source_updated_at").desc_nulls_last(),
                F.col("last_observed_at").desc_nulls_last(),
            )
            .first()
        )
        assert_eq("current state after A -> B -> A", current.state, "OPEN")

        open_version = rows.filter(F.col("state") == "OPEN").first()
        assert_eq(
            "OPEN first source update",
            open_version.first_source_updated_at,
            datetime(2026, 8, 1, 10, 0, 0),
        )
        assert_eq(
            "OPEN last source update",
            open_version.last_source_updated_at,
            datetime(2026, 8, 1, 12, 0, 0),
        )

        print("Business-version rollback acceptance passed.")
    finally:
        spark.sql(f"DROP TABLE IF EXISTS {TARGET}")


if __name__ == "__main__":
    main()
