"""Phase 3C 基于场景的独立 Acceptance Oracle（验收预期）。

这些 Expected Behavior 由人工明确编写，并故意与 Dagster Runtime Event 解耦。
这样未来做真实 Runtime Acceptance 时，可以把实际编排行为与固定 Oracle 比较，
而不是让生产 Policy / Sensor 自己生成“期望结果”再证明自己正确。
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestration.dagster.commerce_dagster.failure_classification import FailureClass
from orchestration.dagster.commerce_dagster.recovery_policy import RecoveryAction, RecoveryObservation

@dataclass(frozen=True)
class AcceptanceScenario:
    """一条人工维护的 Recovery Acceptance 场景。

    输入事实由 ``RecoveryObservation`` 描述；``expected_action`` 与 ``expected_reason_code``
    是独立 Oracle。``proves`` / ``does_not_prove`` 明确记录每个场景能证明什么、不能证明什么。
    ``tuple[str, ...]`` 中的 ``...`` 是 Python typing 的“任意长度”语法，不是代码省略号。
    """

    scenario_id: str
    title_zh: str
    title_en: str
    observation: RecoveryObservation
    expected_action: RecoveryAction
    expected_reason_code: str
    proves: tuple[str, ...]
    does_not_prove: tuple[str, ...]

SCENARIOS = (
    AcceptanceScenario(
        "S01_NORMAL_BEFORE_DEADLINE",
        "正常运行仍在服务预算内",
        "Normal run within freshness budget",
        RecoveryObservation("2026-08-05", False, False, True, False),
        RecoveryAction.WAIT,
        "within_freshness_budget",
        ("Freshness deadline prevents premature recovery",),
        ("The active run will eventually succeed",),
    ),
    AcceptanceScenario(
        "S02_MISSED_SCHEDULE",
        "调度漏触发",
        "Missed schedule / no run",
        RecoveryObservation(
            "2026-08-05",
            True,
            False,
            False,
            False,
            missed_schedule_eligible=True,
        ),
        RecoveryAction.AUTO_REPLAY,
        "missed_schedule_or_no_run",
        ("An overdue exact partition with no owner is replayable once",),
        ("The replay will succeed in a real runtime",),
    ),
    AcceptanceScenario(
        "S03_ACTIVE_RECOVERY_GUARD",
        "已有恢复 Run 正在执行",
        "Active recovery duplicate guard",
        RecoveryObservation("2026-08-05", True, False, True, True, failure_class=FailureClass.TRANSIENT_RUNTIME),
        RecoveryAction.WAIT,
        "active_run_owns_partition",
        ("Historical failure does not create a duplicate run while a new owner is active",),
        ("Dagster run_key persistence",),
    ),
    AcceptanceScenario(
        "S04_INFRA_STILL_DOWN",
        "基础设施仍未恢复",
        "Infrastructure still unavailable",
        RecoveryObservation("2026-08-05", True, False, False, True, failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE, infrastructure_healthy=False),
        RecoveryAction.ALERT_AND_WAIT,
        "infrastructure_unhealthy",
        ("Current health gates recovery",),
        ("External alert delivery",),
    ),
    AcceptanceScenario(
        "S05_INFRA_RECOVERED",
        "基础设施已恢复",
        "Infrastructure recovered after failed run",
        RecoveryObservation("2026-08-05", True, False, False, True, failure_class=FailureClass.INFRASTRUCTURE_UNAVAILABLE, infrastructure_healthy=True),
        RecoveryAction.AUTO_REPLAY,
        "infrastructure_failure_after_runtime_recovered",
        ("Historical cause is separated from current recoverability",),
        ("The recovery materialization will succeed",),
    ),
    AcceptanceScenario(
        "S06_TRANSIENT_RECOVERED",
        "瞬时运行故障后允许一次补跑",
        "Transient runtime failure after recovery",
        RecoveryObservation("2026-08-05", True, False, False, True, failure_class=FailureClass.TRANSIENT_RUNTIME, infrastructure_healthy=True),
        RecoveryAction.AUTO_REPLAY,
        "transient_failure_after_runtime_recovered",
        ("Proven transient failure may cross-run replay",),
        ("A second replay is safe after another failure",),
    ),
    AcceptanceScenario(
        "S07_DETERMINISTIC_CODE",
        "确定性代码错误",
        "Deterministic code failure",
        RecoveryObservation("2026-08-05", True, False, False, True, failure_class=FailureClass.DETERMINISTIC_CODE),
        RecoveryAction.ALERT_MANUAL,
        "deterministic_code_failure",
        ("Deterministic failures fail closed",),
        ("The code fix itself",),
    ),
    AcceptanceScenario(
        "S08_DATA_CONTRACT",
        "数据契约失败",
        "Data contract failure",
        RecoveryObservation("2026-08-05", True, False, False, True, failure_class=FailureClass.DATA_CONTRACT),
        RecoveryAction.ALERT_MANUAL,
        "data_contract_failure",
        ("Quality/schema contract failures do not auto replay",),
        ("Which business record caused the violation",),
    ),
    AcceptanceScenario(
        "S09_UNKNOWN_FAILURE",
        "未知根因",
        "Unknown failure class",
        RecoveryObservation("2026-08-05", True, False, False, True, failure_class=FailureClass.UNKNOWN),
        RecoveryAction.ALERT_MANUAL,
        "unknown_failure_class",
        ("Unknown failures fail closed",),
        ("Root-cause classification accuracy",),
    ),
    AcceptanceScenario(
        "S10_REPLAY_BUDGET_EXHAUSTED",
        "自动恢复预算已耗尽",
        "Cross-run replay budget exhausted",
        RecoveryObservation("2026-08-05", True, False, False, True, failure_class=FailureClass.TRANSIENT_RUNTIME, auto_replay_attempts=1),
        RecoveryAction.ALERT_MANUAL,
        "auto_replay_budget_exhausted",
        ("Automatic recovery cannot loop forever",),
        ("External incident escalation",),
    ),
    AcceptanceScenario(
        "S11_PARTITION_ALREADY_COMPLETE",
        "精确分区已经完整",
        "Exact partition already complete",
        RecoveryObservation("2026-08-05", True, True, False, True, failure_class=FailureClass.TRANSIENT_RUNTIME),
        RecoveryAction.NO_ACTION,
        "partition_already_materialized",
        ("Materialization completeness has precedence over historical failure",),
        ("Physical Iceberg file pruning",),
    ),
    AcceptanceScenario(
        "S12_SUCCESS_BUT_INCOMPLETE",
        "Run 成功但消费者分区不完整",
        "Successful run with incomplete consumer partition",
        RecoveryObservation("2026-08-05", True, False, False, False, successful_run=True),
        RecoveryAction.ALERT_MANUAL,
        "successful_run_without_complete_partition",
        ("Run success is not treated as partition completeness",),
        ("Which missing Mart caused the mismatch",),
    ),    AcceptanceScenario(
        "S13_HISTORICAL_NO_RUN",
        "历史无 Run 不自动回填",
        "Historical no-run gap requires manual backfill",
        RecoveryObservation(
            "2026-08-01",
            True,
            False,
            False,
            False,
            missed_schedule_eligible=False,
        ),
        RecoveryAction.ALERT_MANUAL,
        "historical_no_run_requires_manual_backfill",
        ("No-run absence is not over-classified as a missed schedule",),
        ("Whether an operator should backfill the historical partition",),
    ),

)
