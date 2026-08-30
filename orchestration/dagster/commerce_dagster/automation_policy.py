"""Phase 3C 的纯函数 Timing / Freshness / Recovery Horizon 契约。

这里只定义“什么时候应该运行、什么时候算超时、哪些历史分区进入恢复观察范围”。
它不读取 Dagster Runtime，也不触发 Run；因此可以作为独立 Policy Oracle 做静态验收。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


SHOPIFY_AUTOMATION_TIMEZONE = "UTC"
SHOPIFY_DAILY_SCHEDULE_HOUR = 0
SHOPIFY_DAILY_SCHEDULE_MINUTE = 15
SERVING_DAILY_EXPORT_SCHEDULE_HOUR = 0
SERVING_DAILY_EXPORT_SCHEDULE_MINUTE = 45
SHOPIFY_DAILY_FRESHNESS_DEADLINE_HOUR = 1
SHOPIFY_DAILY_FRESHNESS_DEADLINE_MINUTE = 0
SHOPIFY_DAILY_FRESHNESS_DEADLINE_CRON = (
    f"{SHOPIFY_DAILY_FRESHNESS_DEADLINE_MINUTE} "
    f"{SHOPIFY_DAILY_FRESHNESS_DEADLINE_HOUR} * * *"
)
SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES = (
    SHOPIFY_DAILY_FRESHNESS_DEADLINE_HOUR * 60
    + SHOPIFY_DAILY_FRESHNESS_DEADLINE_MINUTE
    - SHOPIFY_DAILY_SCHEDULE_HOUR * 60
    - SHOPIFY_DAILY_SCHEDULE_MINUTE
)

SHOPIFY_DAILY_JOB_NAME = "shopify_daily_partition_job"
SHOPIFY_DAILY_PARTITION_TAG = "dagster/partition"

SHOPIFY_RECOVERY_SENSOR_MIN_INTERVAL_SECONDS = 300
SHOPIFY_RECOVERY_HORIZON_DAYS = 7
SHOPIFY_RECOVERY_REQUIRED_RUNTIME_SERVICES = ("rustfs", "polaris", "spark-thrift")

SHOPIFY_DAILY_MART_ASSET_KEYS = (
    "orders",
    "order_items",
    "payment_transactions",
    "refunds",
    "refund_items",
    "fulfillments",
    "fulfillment_items",
    "fulfillment_events",
)


def freshness_budget_minutes() -> int:
    """计算正常 Schedule Tick 到 Consumer Freshness Deadline 之间的服务预算分钟数。

输出是 45 分钟这类纯时间契约；若 Deadline 没有晚于正常调度点则直接失败。
工程边界：预算只描述 SLA，不证明任何具体分区已经完成。
"""

    if SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES <= 0:
        raise ValueError("freshness deadline must be later than the normal schedule tick")
    return SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES


def scheduled_tick_utc_for_partition(partition_key: str) -> datetime:
    """计算一个已完成业务日分区对应的正常 Dagster Daily Schedule Tick。

输入 ``YYYY-MM-DD`` 分区键；输出下一自然日 00:15 UTC 的 schedule datetime。
这个函数只计算所有权时间，不读取真实 Schedule / Daemon 事件。
"""

    day = datetime.strptime(partition_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return day + timedelta(
        days=1,
        hours=SHOPIFY_DAILY_SCHEDULE_HOUR,
        minutes=SHOPIFY_DAILY_SCHEDULE_MINUTE,
    )


def partition_key_for_schedule_tick(scheduled_execution_time: datetime) -> str:
    """根据一次正常 Daily Schedule Tick 反推它应该负责的业务日分区。

Dagster 时间语义：00:15 UTC 的 Tick 负责前一个已经完整结束的 UTC Business Day。
输入必须带时区；naive datetime 直接失败，避免本地时区造成分区错位。
"""

    if scheduled_execution_time.tzinfo is None:
        raise ValueError("scheduled_execution_time must be timezone-aware")
    tick_utc = scheduled_execution_time.astimezone(timezone.utc)
    return (tick_utc.date() - timedelta(days=1)).isoformat()


def partition_deadline_utc(partition_key: str) -> datetime:
    """计算一个业务日分区的 Consumer Freshness Deadline。
    
    输入：``YYYY-MM-DD`` partition_key。
    输出：UTC deadline datetime。
    时间语义：Schedule 触发时间与 Freshness Deadline 是两个概念；deadline 表示消费者允许等待到什么时候。
    """
    day = datetime.strptime(partition_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return day + timedelta(
        days=1,
        hours=SHOPIFY_DAILY_FRESHNESS_DEADLINE_HOUR,
        minutes=SHOPIFY_DAILY_FRESHNESS_DEADLINE_MINUTE,
    )


def overdue_partition_keys(
    now_utc: datetime,
    *,
    horizon_days: int = SHOPIFY_RECOVERY_HORIZON_DAYS,
) -> tuple[str, ...]:
    """计算 Recovery Horizon 内已经超过 Freshness Deadline 的业务分区。
    
    输入：当前 UTC 时间与 horizon_days。
    输出：按时间排序的 overdue partition tuple。
    工程边界：这里只发现“超时分区”，是否自动恢复仍由 Recovery Policy + Exact Partition State 决定。
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    now_utc = now_utc.astimezone(timezone.utc)
    if horizon_days < 1:
        return ()
    latest_candidate = (now_utc - timedelta(days=1)).date()
    keys = []
    for age in range(horizon_days - 1, -1, -1):
        day = latest_candidate - timedelta(days=age)
        key = day.isoformat()
        if partition_deadline_utc(key) <= now_utc:
            keys.append(key)
    return tuple(keys)


def latest_overdue_partition_key(now_utc: datetime) -> str | None:
    """返回 Recovery Horizon 中最新一个已经超过 Consumer Deadline 的业务分区。

输出可能为 ``None``；这个结果用于“无历史 Run”场景的自动恢复边界，
避免把很久以前从未部署过的日期误判成 Missed Schedule。
"""

    keys = overdue_partition_keys(now_utc)
    return keys[-1] if keys else None


def missed_schedule_auto_replay_eligible(
    partition_key: str,
    now_utc: datetime,
) -> bool:
    """把“没有历史 Run”的自动恢复严格限制在最新 overdue 分区。

    没有历史 Dagster Run 并不能证明 Schedule 漏跑，也可能当时系统尚未部署。
    因此旧日期的 no-run gap 必须走显式 / 人工 Backfill，不能被 Sensor 静默自动 Replay。
    """

    return partition_key == latest_overdue_partition_key(now_utc)

