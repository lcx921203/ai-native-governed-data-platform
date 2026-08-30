"""行为事件 Flink Job 的环境配置。

时间策略都集中在这里，避免 Watermark / allowed lateness / State TTL 散落在业务代码里。
生产上这些值应根据延迟分布、实时 SLA、Checkpoint 大小等指标调优。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前值={raw!r}") from exc


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    value = raw.strip().lower()
    if value in {"true", "1", "yes", "on"}:
        return True
    if value in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false，当前值={raw!r}")


@dataclass(frozen=True)
class BehaviorJobConfig:
    kafka_bootstrap_servers: str
    kafka_topic: str
    kafka_group_id: str
    max_out_of_orderness_seconds: int
    idle_partition_seconds: int
    allowed_lateness_seconds: int
    dedup_state_ttl_hours: int
    checkpoint_interval_ms: int
    checkpoint_timeout_ms: int
    checkpoint_min_pause_ms: int
    checkpoint_storage: str
    savepoint_storage: str
    restart_attempts: int
    restart_delay_ms: int
    enable_unaligned_checkpoints: bool


def load_config() -> BehaviorJobConfig:
    return BehaviorJobConfig(
        kafka_bootstrap_servers=os.environ["KAFKA_BOOTSTRAP_SERVERS"],
        kafka_topic=os.getenv("BEHAVIOR_KAFKA_TOPIC", "commerce.behavior.events"),
        kafka_group_id=os.getenv("BEHAVIOR_KAFKA_GROUP_ID", "behavior-lakehouse-v1"),
        max_out_of_orderness_seconds=_int("BEHAVIOR_MAX_OUT_OF_ORDERNESS_SECONDS", 120),
        idle_partition_seconds=_int("BEHAVIOR_IDLE_PARTITION_SECONDS", 60),
        allowed_lateness_seconds=_int("BEHAVIOR_ALLOWED_LATENESS_SECONDS", 300),
        dedup_state_ttl_hours=_int("BEHAVIOR_DEDUP_STATE_TTL_HOURS", 24),
        checkpoint_interval_ms=_int("FLINK_CHECKPOINT_INTERVAL_MS", 60_000),
        checkpoint_timeout_ms=_int("FLINK_CHECKPOINT_TIMEOUT_MS", 10 * 60_000),
        checkpoint_min_pause_ms=_int("FLINK_CHECKPOINT_MIN_PAUSE_MS", 30_000),
        checkpoint_storage=os.environ["FLINK_CHECKPOINT_STORAGE"],
        savepoint_storage=os.environ["FLINK_SAVEPOINT_STORAGE"],
        restart_attempts=_int("FLINK_RESTART_ATTEMPTS", 3),
        restart_delay_ms=_int("FLINK_RESTART_DELAY_MS", 10_000),
        # Unaligned Checkpoint 只在严重 Backpressure 时按需开启，不默认开启。
        enable_unaligned_checkpoints=_bool("FLINK_ENABLE_UNALIGNED_CHECKPOINTS", False),
    )
