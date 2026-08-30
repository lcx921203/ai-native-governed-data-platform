"""把 DataStream 结果注册成 Table View，并通过 Iceberg Flink Sink 写多个表。

为什么这里借 Table API 做 Sink：
DataStream API 负责核心状态/时间逻辑；Iceberg 官方 Flink Sink 负责 checkpoint-aware commit。
StatementSet.attach_as_datastream() 会把多个 INSERT 作为同一个 Job Graph 的 Sink transformations。
"""

from __future__ import annotations

import os

from pyflink.table import DataTypes, Schema, StreamTableEnvironment


def _sql_literal(value: str) -> str:
    """把普通字符串安全转成 Flink SQL 单引号字面量。
    
    输入：原字符串。
    输出：单引号转义后的 SQL literal。
    工程目的：生成 DDL 时避免配置值中的单引号破坏 SQL 语法。
    """
    return value.replace("'", "''")


def ensure_iceberg_objects(t_env: StreamTableEnvironment) -> None:
    """创建行为流写入所需的 Polaris Catalog、Database 与 Iceberg 表。
    
    输入：StreamTableEnvironment。
    输出：通过 execute_sql 注册/创建运行时对象。
    Flink/Iceberg API：Catalog 负责找到 Iceberg 表，Sink 表定义决定主流/side output 的物理契约。
    工程边界：DDL 成功与端到端 exactly-once 仍是不同证据层级。
    """
    uri = _sql_literal(os.environ["POLARIS_URI"])
    warehouse = _sql_literal(os.environ.get("POLARIS_CATALOG_NAME", "commerce_catalog"))
    client_id = _sql_literal(os.environ["POLARIS_CLIENT_ID"])
    client_secret = _sql_literal(os.environ["POLARIS_CLIENT_SECRET"])

    t_env.execute_sql(
        f"""
        CREATE CATALOG polaris WITH (
          'type'='iceberg',
          'catalog-type'='rest',
          'uri'='{uri}',
          'warehouse'='{warehouse}',
          'credential'='{client_id}:{client_secret}',
          'scope'='PRINCIPAL_ROLE:ALL',
          'header.X-Iceberg-Access-Delegation'='vended-credentials'
        )
        """
    )

    for namespace in ("raw", "source", "ops", "realtime"):
        t_env.execute_sql(f"CREATE DATABASE IF NOT EXISTS polaris.{namespace}")

    # 原始观察（Raw Observation）：传输证据，保持 append-only；即使 event_id 重复也保留。
    t_env.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS polaris.raw.behavior_event_observation (
          payload STRING,
          observed_at_ms BIGINT
        ) WITH ('format-version'='2')
        """
    )

    # 规范事件（Canonical Event）：event_id 是业务幂等键；DataStream 已去重，这里仍声明 v2 upsert 保护重放。
    t_env.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS polaris.source.behavior_event (
          event_id STRING NOT NULL,
          event_name STRING,
          user_id STRING,
          session_id STRING,
          item_id STRING,
          store_id STRING,
          event_time_ms BIGINT,
          collector_received_at_ms BIGINT,
          page_url STRING,
          device_type STRING,
          properties_json STRING,
          raw_json STRING,
          PRIMARY KEY (event_id) NOT ENFORCED
        ) WITH (
          'format-version'='2',
          'write.upsert.enabled'='true'
        )
        """
    )

    t_env.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS polaris.ops.behavior_event_invalid (
          payload STRING,
          error_reason STRING,
          observed_at_ms BIGINT
        ) WITH ('format-version'='2')
        """
    )

    t_env.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS polaris.ops.behavior_event_too_late (
          event_id STRING,
          event_name STRING,
          user_id STRING,
          session_id STRING,
          item_id STRING,
          store_id STRING,
          event_time_ms BIGINT,
          collector_received_at_ms BIGINT,
          page_url STRING,
          device_type STRING,
          properties_json STRING,
          raw_json STRING
        ) WITH ('format-version'='2')
        """
    )

    # allowed lateness 可能产生 late firing，同一窗口会输出“更新后的结果”。
    # 所以这里必须按 (item_id, window_start_ms, window_end_ms) UPSERT，而不是把每次 firing 当新事实 append。
    t_env.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS polaris.realtime.product_view_5m (
          item_id STRING NOT NULL,
          window_start_ms BIGINT NOT NULL,
          window_end_ms BIGINT NOT NULL,
          view_count BIGINT,
          emitted_at_ms BIGINT,
          PRIMARY KEY (item_id, window_start_ms, window_end_ms) NOT ENFORCED
        ) WITH (
          'format-version'='2',
          'write.upsert.enabled'='true'
        )
        """
    )


def attach_iceberg_sinks(
    t_env: StreamTableEnvironment,
    *,
    raw_observations,
    canonical_events,
    invalid_events,
    too_late_events,
    product_view_5m,
) -> None:
    """把五条 DataStream 接到同一个 StatementSet。"""

    t_env.create_temporary_view(
        "v_raw_behavior_observation",
        raw_observations,
        Schema.new_builder()
        .column("payload", DataTypes.STRING())
        .column("observed_at_ms", DataTypes.BIGINT())
        .build(),
    )
    t_env.create_temporary_view(
        "v_canonical_behavior_event",
        canonical_events,
        Schema.new_builder()
        .column("event_id", DataTypes.STRING())
        .column("event_name", DataTypes.STRING())
        .column("user_id", DataTypes.STRING())
        .column("session_id", DataTypes.STRING())
        .column("item_id", DataTypes.STRING())
        .column("store_id", DataTypes.STRING())
        .column("event_time_ms", DataTypes.BIGINT())
        .column("collector_received_at_ms", DataTypes.BIGINT())
        .column("page_url", DataTypes.STRING())
        .column("device_type", DataTypes.STRING())
        .column("properties_json", DataTypes.STRING())
        .column("raw_json", DataTypes.STRING())
        .build(),
    )
    t_env.create_temporary_view(
        "v_invalid_behavior_event",
        invalid_events,
        Schema.new_builder()
        .column("payload", DataTypes.STRING())
        .column("error_reason", DataTypes.STRING())
        .column("observed_at_ms", DataTypes.BIGINT())
        .build(),
    )
    t_env.create_temporary_view(
        "v_too_late_behavior_event",
        too_late_events,
        Schema.new_builder()
        .column("event_id", DataTypes.STRING())
        .column("event_name", DataTypes.STRING())
        .column("user_id", DataTypes.STRING())
        .column("session_id", DataTypes.STRING())
        .column("item_id", DataTypes.STRING())
        .column("store_id", DataTypes.STRING())
        .column("event_time_ms", DataTypes.BIGINT())
        .column("collector_received_at_ms", DataTypes.BIGINT())
        .column("page_url", DataTypes.STRING())
        .column("device_type", DataTypes.STRING())
        .column("properties_json", DataTypes.STRING())
        .column("raw_json", DataTypes.STRING())
        .build(),
    )
    t_env.create_temporary_view(
        "v_product_view_5m",
        product_view_5m,
        Schema.new_builder()
        .column("item_id", DataTypes.STRING())
        .column("window_start_ms", DataTypes.BIGINT())
        .column("window_end_ms", DataTypes.BIGINT())
        .column("view_count", DataTypes.BIGINT())
        .column("emitted_at_ms", DataTypes.BIGINT())
        .build(),
    )

    statement_set = t_env.create_statement_set()
    statement_set.add_insert_sql(
        "INSERT INTO polaris.raw.behavior_event_observation SELECT * FROM v_raw_behavior_observation"
    )
    statement_set.add_insert_sql(
        "INSERT INTO polaris.source.behavior_event SELECT * FROM v_canonical_behavior_event"
    )
    statement_set.add_insert_sql(
        "INSERT INTO polaris.ops.behavior_event_invalid SELECT * FROM v_invalid_behavior_event"
    )
    statement_set.add_insert_sql(
        "INSERT INTO polaris.ops.behavior_event_too_late SELECT * FROM v_too_late_behavior_event"
    )
    statement_set.add_insert_sql(
        "INSERT INTO polaris.realtime.product_view_5m SELECT * FROM v_product_view_5m"
    )

    # 只“附加”到现有 StreamExecutionEnvironment；真正提交在 job.py 的 env.execute()。
    statement_set.attach_as_datastream()
