"""电商行为事件 PyFlink DataStream Job。

这条源码把 Flink 面试里经常单独背的概念放进一条真实数据链：
Kafka Source → Event Time / Watermark → Keyed State → TTL → Window → Allowed Lateness
→ Side Output → Checkpoint / Recovery → Iceberg Sink。

重要：当前包只做 source/static validation，没有在本会话里真实启动 Flink 集群。
"""

from __future__ import annotations

import time

from pyflink.common import Configuration, Duration, Time, WatermarkStrategy
from pyflink.common.serialization import SimpleStringSchema
from pyflink.common.typeinfo import Types
from pyflink.datastream import StreamExecutionEnvironment
from pyflink.datastream.checkpointing_mode import CheckpointingMode
from pyflink.datastream.connectors.kafka import (
    KafkaOffsetResetStrategy,
    KafkaOffsetsInitializer,
    KafkaSource,
)
from pyflink.datastream.externalized_checkpoint_retention import ExternalizedCheckpointRetention
from pyflink.datastream.output_tag import OutputTag
from pyflink.datastream.window import TumblingEventTimeWindows
from pyflink.table import StreamTableEnvironment

from ingestion.behavior.flink.config import load_config
from ingestion.behavior.flink.functions import (
    AddWindowMetadata,
    DeduplicateByEventId,
    EventTimestampAssigner,
    ParseBehaviorEvent,
    ProductViewCount,
)
from ingestion.behavior.flink.iceberg_sinks import attach_iceberg_sinks, ensure_iceberg_objects
from ingestion.behavior.flink.types import (
    BEHAVIOR_EVENT_TYPE,
    EVENT_ID,
    EVENT_NAME,
    EVENT_TIME_MS,
    ITEM_ID,
    INVALID_EVENT_TYPE,
    PRODUCT_VIEW_WINDOW_TYPE,
    RAW_OBSERVATION_TYPE,
)


INVALID_TAG = OutputTag("invalid-behavior-event", INVALID_EVENT_TYPE)
TOO_LATE_TAG = OutputTag("too-late-product-view", BEHAVIOR_EVENT_TYPE)


def configure_fault_tolerance(env: StreamExecutionEnvironment, cfg) -> None:
    """配置 Exactly-once Checkpoint、RocksDB State 与失败重启。

    关键理解：Checkpoint 保存的是“一致的 Source Position + Managed State”。
    任务挂掉后，会恢复最近成功 Checkpoint；最近 Checkpoint 后处理过的 Kafka 数据可能 Replay。
    Exactly-once 保证的是恢复后的状态/最终结果等价于无故障执行，不是物理上只经过 CPU 一次。
    """
    env.enable_checkpointing(cfg.checkpoint_interval_ms)
    checkpoint = env.get_checkpoint_config()
    checkpoint.set_checkpointing_mode(CheckpointingMode.EXACTLY_ONCE)
    checkpoint.set_checkpoint_timeout(cfg.checkpoint_timeout_ms)
    checkpoint.set_min_pause_between_checkpoints(cfg.checkpoint_min_pause_ms)
    checkpoint.set_max_concurrent_checkpoints(1)
    checkpoint.set_tolerable_checkpoint_failure_number(2)
    checkpoint.set_checkpoint_storage(cfg.checkpoint_storage)
    checkpoint.enable_externalized_checkpoints(
        ExternalizedCheckpointRetention.RETAIN_ON_CANCELLATION
    )

    # Unaligned Checkpoint 不是“越高级越要开”。它主要用于严重 Backpressure 下缩短 Barrier 对齐等待；
    # 本项目默认关闭。开启时保持 EXACTLY_ONCE + max concurrent checkpoints = 1。
    if cfg.enable_unaligned_checkpoints:
        checkpoint.enable_unaligned_checkpoints()

    # Flink 1.20 更推荐用 Configuration 配 State Backend / Restart Strategy，
    # 而不是继续调用已经标记 deprecated 的 set_state_backend / set_restart_strategy。
    # 这里仍然是“作业级配置”：不会修改整个集群的全局 flink-conf.yaml。
    runtime_config = Configuration()
    runtime_config.set_string("state.backend.type", "rocksdb")
    runtime_config.set_boolean("execution.checkpointing.incremental", True)
    runtime_config.set_string("restart-strategy.type", "fixed-delay")
    runtime_config.set_integer("restart-strategy.fixed-delay.attempts", cfg.restart_attempts)
    runtime_config.set_string(
        "restart-strategy.fixed-delay.delay",
        f"{cfg.restart_delay_ms} ms",
    )
    env.configure(runtime_config)

    # Savepoint 与 Checkpoint 不是一回事：Checkpoint 主要自动容错；Savepoint 主要用于升级/迁移。
    env.set_default_savepoint_directory(cfg.savepoint_storage)


def build_kafka_source(cfg) -> KafkaSource:
    """构造 Kafka Source。

    ``committed_offsets`` 只决定“没有 Flink 恢复状态时”的起始位置；正常故障恢复使用 Checkpoint
    中的 partition offsets。EARLIEST 是首次部署没有 committed offset 时的兜底。
    """
    return (
        KafkaSource.builder()
        .set_bootstrap_servers(cfg.kafka_bootstrap_servers)
        .set_topics(cfg.kafka_topic)
        .set_group_id(cfg.kafka_group_id)
        .set_starting_offsets(
            KafkaOffsetsInitializer.committed_offsets(
                KafkaOffsetResetStrategy.EARLIEST
            )
        )
        .set_value_only_deserializer(SimpleStringSchema())
        .build()
    )


def build_job(env: StreamExecutionEnvironment, t_env: StreamTableEnvironment, cfg) -> None:
    """组装行为事件 PyFlink DataStream 拓扑。
    
    输入：StreamExecutionEnvironment、StreamTableEnvironment 与运行配置。
    输出：把 Source → 解析校验 → Watermark → 去重 State → 窗口 → Main/Side Output → Iceberg Sink 连接起来。
    Flink API：Watermark 决定 Event Time 进度，Keyed State 负责 event_id 去重，Side Output 隔离 invalid/too-late。
    工程边界：拓扑定义存在不等于 Exactly-once 已真实验证；Checkpoint/Kafka/Iceberg Runtime 仍需运行证据。
    """
    source = build_kafka_source(cfg)

    # Kafka Source 先输出原始 JSON。这里不直接在 Source 上定义业务 Watermark，
    # 因为 event_time 还藏在 JSON 里，需要 Parse 后才能取到真正业务时间。
    raw_json = env.from_source(
        source,
        WatermarkStrategy.no_watermarks(),
        "behavior-kafka-source",
    )

    # Raw Observation 在去重之前分叉：即使网络重试导致同一 event_id 被观察两次，Raw 也保留两次证据。
    raw_observations = raw_json.map(
        lambda value: (value, int(time.time() * 1000)),
        output_type=RAW_OBSERVATION_TYPE,
    ).name("raw-behavior-observation")

    parsed = raw_json.process(
        ParseBehaviorEvent(INVALID_TAG),
        output_type=BEHAVIOR_EVENT_TYPE,
    ).name("parse-and-validate")
    invalid_events = parsed.get_side_output(INVALID_TAG)

    # Watermark = “事件时间进度的判断”，不是简单过滤迟到数据。
    # bounded out-of-orderness 给乱序留 2 分钟（默认）；with_idleness 防止空闲 Kafka 分区拖住全局 Watermark。
    with_event_time = parsed.assign_timestamps_and_watermarks(
        WatermarkStrategy.for_bounded_out_of_orderness(
            Duration.of_seconds(cfg.max_out_of_orderness_seconds)
        )
        .with_timestamp_assigner(EventTimestampAssigner())
        .with_idleness(Duration.of_seconds(cfg.idle_partition_seconds))
    ).name("event-time-watermark")

    # key_by(event_id) 后 KeyedProcessFunction 才能访问 Keyed State。
    # 去重 State 会被 Checkpoint；任务恢复时 State 与 Kafka offset 一起回到同一个一致性点。
    canonical_events = (
        with_event_time.key_by(lambda event: event[EVENT_ID], key_type=Types.STRING())
        .process(
            DeduplicateByEventId(cfg.dedup_state_ttl_hours),
            output_type=BEHAVIOR_EVENT_TYPE,
        )
        .name("event-id-dedup")
    )

    # 只把 product_view 放进示例实时窗口；canonical event 本身仍完整写 source.behavior_event。
    product_views = canonical_events.filter(
        lambda event: event[EVENT_NAME] == "product_view" and bool(event[ITEM_ID])
    ).name("product-view-only")

    product_view_5m = (
        product_views.key_by(lambda event: event[ITEM_ID], key_type=Types.STRING())
        .window(TumblingEventTimeWindows.of(Time.minutes(5)))
        # Watermark 已过 window_end 后，再给 5 分钟“实时修正预算”。
        .allowed_lateness(cfg.allowed_lateness_seconds * 1000)
        # 超过 window_end + allowed_lateness 的合法事件进入独立 Side Output，而不是直接丢弃。
        # 注意：Side Output 是 Window Operator 的“实时窗口治理出口”；事件在更前面已经进入
        # canonical source.behavior_event，因此离线/dbt 仍可用权威明细做 reconciliation（校正）。
        .side_output_late_data(TOO_LATE_TAG)
        .aggregate(
            ProductViewCount(),
            window_function=AddWindowMetadata(),
            accumulator_type=Types.LONG(),
            output_type=PRODUCT_VIEW_WINDOW_TYPE,
        )
        .name("product-view-5m")
    )
    # Python API 必须用同一个 OutputTag 再 get_side_output()；否则旁路结果会混进主流，
    # 类型不同还可能让作业直接失败。
    too_late_events = product_view_5m.get_side_output(TOO_LATE_TAG)

    ensure_iceberg_objects(t_env)
    attach_iceberg_sinks(
        t_env,
        raw_observations=raw_observations,
        canonical_events=canonical_events,
        invalid_events=invalid_events,
        too_late_events=too_late_events,
        product_view_5m=product_view_5m,
    )


def main() -> None:
    """创建 PyFlink 执行环境、加载配置并提交行为事件 Job。
    
    输入：环境变量 / Flink Runtime。
    输出：向 Flink 提交名为行为流的执行图。
    工程边界：真实提交是否成功、Checkpoint 是否完成、Sink 是否 exactly-once 必须由 Runtime Evidence 证明。
    """
    cfg = load_config()
    env = StreamExecutionEnvironment.get_execution_environment()
    # 先把 State Backend / Checkpoint / Restart Strategy 配进 Stream Env，
    # 再从同一个 Env 创建 Table Environment，避免 Sink 侧拿到不同的运行配置。
    configure_fault_tolerance(env, cfg)
    t_env = StreamTableEnvironment.create(env)

    build_job(env, t_env, cfg)
    env.execute("commerce-behavior-streaming")


if __name__ == "__main__":
    main()
