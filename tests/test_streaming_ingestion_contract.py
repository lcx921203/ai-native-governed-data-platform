from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_new_production_ingestion_paths_have_no_fixture_branch():
    for rel in ["ingestion/mysql_cdc", "ingestion/behavior"]:
        files = [p for p in (ROOT / rel).rglob("*") if p.is_file()]
        assert files
        assert all("fixture" not in p.name.lower() for p in files)


def test_mysql_cdc_sql_expresses_snapshot_binlog_and_iceberg_upsert():
    sql = read("ingestion/mysql_cdc/flink/item_store_cdc.sql.tmpl")
    assert sql.count("'connector' = 'mysql-cdc'") == 2
    assert sql.count("'scan.startup.mode' = 'initial'") == 2
    assert "'server-id' = '5401-5408'" in sql
    assert "'server-id' = '5411-5418'" in sql
    assert sql.count("'format-version' = '2'") >= 2
    assert sql.count("'write.upsert.enabled' = 'true'") >= 2
    assert "BEGIN STATEMENT SET" in sql
    assert "'execution.checkpointing.mode' = 'EXACTLY_ONCE'" in sql
    assert "'execution.checkpointing.dir' = '${FLINK_CHECKPOINT_STORAGE}'" in sql


def test_behavior_job_covers_core_flink_interview_contracts():
    job = read("ingestion/behavior/flink/job.py")
    funcs = read("ingestion/behavior/flink/functions.py")
    sinks = read("ingestion/behavior/flink/iceberg_sinks.py")

    for text in [job, funcs, sinks]:
        ast.parse(text)

    required_job_tokens = [
        "KafkaSource.builder()",
        "CheckpointingMode.EXACTLY_ONCE",
        'runtime_config.set_string("state.backend.type", "rocksdb")',
        'runtime_config.set_boolean("execution.checkpointing.incremental", True)',
        "KafkaOffsetResetStrategy.EARLIEST",
        "for_bounded_out_of_orderness",
        "with_idleness",
        "key_by",
        "allowed_lateness",
        "side_output_late_data",
        "get_side_output",
        "enable_externalized_checkpoints",
        'runtime_config.set_string("restart-strategy.type", "fixed-delay")',
    ]
    for token in required_job_tokens:
        assert token in job, token

    assert "ValueStateDescriptor" in funcs
    assert "StateTtlConfig" in funcs
    assert "NeverReturnExpired" in funcs
    assert "ProductViewCount" in funcs
    assert "ProcessWindowFunction" in funcs

    # invalid（契约坏数据）和 too-late（合法但超过实时修正预算）必须是两条不同治理流。
    assert "behavior_event_invalid" in sinks
    assert "behavior_event_too_late" in sinks
    assert "product_view_5m" in sinks
    assert "write.upsert.enabled" in sinks


def test_collector_uses_kafka_idempotent_producer_but_keeps_flink_dedup():
    producer = read("ingestion/behavior/collector/producer.py")
    assert '"enable.idempotence": True' in producer
    assert '"acks": "all"' in producer
    assert 'event_id' in producer
    assert 'on_delivery=on_delivery' in producer
    assert '等待 Kafka Broker ACK 超时' in producer


def test_production_dbt_master_marts_read_cdc_staging_not_seed():
    items = read("dbt/mercaso_dbt/models/marts/commerce/items.sql")
    stores = read("dbt/mercaso_dbt/models/marts/commerce/stores.sql")
    assert "stg_master__items" in items and "seed_items" not in items
    assert "stg_master__stores" in stores and "seed_stores" not in stores
