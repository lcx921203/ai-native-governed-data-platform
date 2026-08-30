"""有界 Cross-run Recovery 的纯函数决策策略。

Policy 只根据已经收集的运行事实决定 NO_ACTION / WAIT / AUTO_REPLAY / ALERT；
它不读取 Dagster Storage，也不真正发起 Run，从而让 Recovery 规则可以被独立 Oracle 验证。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

try:
    from .failure_classification import FailureClass
except ImportError:  # pure-policy tests may import this module directly
    from failure_classification import FailureClass


class RecoveryAction(str, Enum):
    """Recovery Policy 允许输出的有限动作集合。

    使用 Enum 而不是自由字符串，避免 Sensor / Acceptance 在动作名称上发生静默拼写漂移。
    """

    NO_ACTION = "no_action"
    WAIT = "wait"
    AUTO_REPLAY = "auto_replay"
    ALERT_AND_WAIT = "alert_and_wait"
    ALERT_MANUAL = "alert_manual"


@dataclass(frozen=True)
class RecoveryObservation:
    """一次 Recovery 决策所需的受控事实输入。

    Grain：一个 ``partition_key`` 一条 Observation。
    字段同时覆盖 Freshness、9/9 exact partition、Run 状态、FailureClass、当前基础设施健康与 Replay Budget。
    ``frozen=True`` 防止进入纯决策函数后被调用方原地修改。
    """

    partition_key: str
    freshness_overdue: bool
    materialized: bool
    active_run: bool
    failed_run: bool
    successful_run: bool = False
    failure_class: FailureClass = FailureClass.NONE
    infrastructure_healthy: bool = True
    auto_replay_attempts: int = 0
    missed_schedule_eligible: bool = False

    def __post_init__(self) -> None:
        """校验 Recovery 输入事实的基本不变量。
        
        工程边界：partition_key 不能为空、replay 次数不能为负；非法状态在进入决策树前直接失败。
        """
        if not self.partition_key:
            raise ValueError("partition_key is required")
        if self.auto_replay_attempts < 0:
            raise ValueError("auto_replay_attempts cannot be negative")


@dataclass(frozen=True)
class RecoveryDecision:
    """纯 Recovery Policy 的输出结果。

    ``action`` 决定允许做什么；``reason_code`` 给机器消费；``explanation`` 给日志 / 人工排障阅读。
    这个对象仍只是“批准动作”，不代表对应 Run 已经 EXECUTED。
    """

    action: RecoveryAction
    reason_code: str
    explanation: str

    @property
    def should_auto_replay(self) -> bool:
        """把 RecoveryAction 转换成便于调用方判断的布尔属性。
        
        输出：只有 ``AUTO_REPLAY`` 时为 True。
        工程目的：调用方不需要重复比较字符串，降低分支写错概率。
        """
        return self.action is RecoveryAction.AUTO_REPLAY


DEFAULT_MAX_AUTO_REPLAYS = 1


def decide_recovery(
    observation: RecoveryObservation,
    *,
    max_auto_replays: int = DEFAULT_MAX_AUTO_REPLAYS,
) -> RecoveryDecision:
    """根据已收集的运行事实做纯函数式 Cross-run Recovery 决策。
    
    输入：Freshness、Exact Materialization、Active/Failed/Successful Run、FailureClass、当前基础设施健康、Replay Budget。
    输出：RecoveryDecision，不执行任何 Dagster Run。
    决策原则：Exact Partition Complete 优先；Active Run 不抢占；SUCCESS 但分区不完整必须人工；UNKNOWN Fail Closed；只有证明可恢复且预算未耗尽时才 AUTO_REPLAY。
    工程边界：Policy 只决定“允许做什么”，真正触发 Run 属于 Sensor/Runtime 层。
    """
    if max_auto_replays < 0:
        raise ValueError("max_auto_replays cannot be negative")
    if observation.materialized:
        return RecoveryDecision(
            RecoveryAction.NO_ACTION,
            "partition_already_materialized",
            "Exact consumer partition is complete.",
        )
    if not observation.freshness_overdue:
        return RecoveryDecision(
            RecoveryAction.WAIT,
            "within_freshness_budget",
            "Consumer deadline has not been breached.",
        )
    if observation.active_run:
        return RecoveryDecision(
            RecoveryAction.WAIT,
            "active_run_owns_partition",
            "A run already owns this partition.",
        )
    if not observation.infrastructure_healthy:
        return RecoveryDecision(
            RecoveryAction.ALERT_AND_WAIT,
            "infrastructure_unhealthy",
            "Current infrastructure is still unavailable.",
        )
    if observation.auto_replay_attempts >= max_auto_replays:
        return RecoveryDecision(
            RecoveryAction.ALERT_MANUAL,
            "auto_replay_budget_exhausted",
            "Automatic cross-run replay budget is exhausted.",
        )
    if observation.successful_run and not observation.materialized:
        return RecoveryDecision(
            RecoveryAction.ALERT_MANUAL,
            "successful_run_without_complete_partition",
            "A successful run exists but the exact consumer partition is incomplete.",
        )
    if not observation.failed_run:
        if observation.missed_schedule_eligible:
            return RecoveryDecision(
                RecoveryAction.AUTO_REPLAY,
                "missed_schedule_or_no_run",
                "Newest overdue partition has no run owner; launch one bounded replay.",
            )
        return RecoveryDecision(
            RecoveryAction.ALERT_MANUAL,
            "historical_no_run_requires_manual_backfill",
            "No historical run does not prove a missed schedule; require explicit backfill.",
        )
    if observation.failure_class is FailureClass.TRANSIENT_RUNTIME:
        return RecoveryDecision(
            RecoveryAction.AUTO_REPLAY,
            "transient_failure_after_runtime_recovered",
            "Transient failure and current infrastructure is healthy.",
        )
    if observation.failure_class is FailureClass.INFRASTRUCTURE_UNAVAILABLE:
        return RecoveryDecision(
            RecoveryAction.AUTO_REPLAY,
            "infrastructure_failure_after_runtime_recovered",
            "Historical infrastructure failure is now recoverable because current infrastructure is healthy.",
        )
    if observation.failure_class is FailureClass.DETERMINISTIC_CODE:
        return RecoveryDecision(
            RecoveryAction.ALERT_MANUAL,
            "deterministic_code_failure",
            "Repeating deterministic code failure will not repair it.",
        )
    if observation.failure_class is FailureClass.DATA_CONTRACT:
        return RecoveryDecision(
            RecoveryAction.ALERT_MANUAL,
            "data_contract_failure",
            "Investigate/quarantine the contract failure before replay.",
        )
    return RecoveryDecision(
        RecoveryAction.ALERT_MANUAL,
        "unknown_failure_class",
        "Root cause is not proven replay-safe; fail closed.",
    )


def recovery_run_key(partition_key: str, attempt: int) -> str:
    """生成确定性的 Recovery Run Key。
    
    输入：partition_key 与第几次自动 replay。
    输出：``shopify-daily-recovery:<partition>:attempt-N``。
    Dagster 语义：稳定 run_key 用于幂等去重，避免 Sensor 重复评估时发起同一次恢复。
    """
    if not partition_key:
        raise ValueError("partition_key is required")
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return f"shopify-daily-recovery:{partition_key}:attempt-{attempt}"
