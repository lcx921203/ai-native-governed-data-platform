"""有界 Cross-run Recovery Sensor；决策 Policy 明确留在 Sensor 外部。

Sensor 负责读取 overdue 分区与 Runtime State、调用纯 ``decide_recovery``，并且一次 Evaluation
最多发出一个稳定 ``run_key`` 的 RunRequest。Policy 与执行分离，避免 Sensor 自己成为不可测试的决策黑盒。
"""

from __future__ import annotations

from datetime import datetime, timezone

import dagster as dg

from .automation_policy import (
    SHOPIFY_RECOVERY_REQUIRED_RUNTIME_SERVICES,
    SHOPIFY_RECOVERY_SENSOR_MIN_INTERVAL_SECONDS,
    missed_schedule_auto_replay_eligible,
    overdue_partition_keys,
)
from .jobs import shopify_daily_partition_job
from .project import PROJECT_ROOT
from .recovery_policy import RecoveryAction, decide_recovery, recovery_run_key
from .recovery_state_current import (
    AUTO_RECOVERY_TAG_VALUE,
    RECOVERY_ATTEMPT_TAG,
    RECOVERY_REASON_TAG,
    RECOVERY_TAG,
    collect_partition_recovery_state,
)
from .runtime_health import docker_compose_services_running


def utc_now() -> datetime:
    """返回当前 UTC 时间，并保留成可替换函数供 Acceptance 固定时钟。
    
    生产 Sensor 调用真实时间；测试可以 patch 它以稳定复现 Deadline / overdue 场景。
    """

    return datetime.now(timezone.utc)


@dg.sensor(
    job=shopify_daily_partition_job,
    minimum_interval_seconds=SHOPIFY_RECOVERY_SENSOR_MIN_INTERVAL_SECONDS,
    default_status=dg.DefaultSensorStatus.STOPPED,
    description=(
        "Inspect overdue exact partitions and launch at most one replay-safe recovery run."
    ),
)
def shopify_daily_recovery_sensor(context: dg.SensorEvaluationContext):
    """扫描 overdue 分区，并在一次 Sensor Evaluation 中最多发出一个被证明安全的 Recovery RunRequest。
    
    Sensor 读取当前基础设施与 exact-partition state，把决策交给纯 Recovery Policy；告警分支只记日志，只有 ``AUTO_REPLAY`` 才返回稳定 run_key 的 RunRequest。没有安全候选时返回 SkipReason。
    """
    now_utc = utc_now()
    candidate_keys = overdue_partition_keys(now_utc)
    if not candidate_keys:
        return dg.SkipReason("No daily partition has crossed the recovery deadline yet.")

    infrastructure_healthy = docker_compose_services_running(
        PROJECT_ROOT,
        SHOPIFY_RECOVERY_REQUIRED_RUNTIME_SERVICES,
    )
    summaries: list[str] = []
    for partition_key in candidate_keys:
        runtime_state = collect_partition_recovery_state(
            context.instance,
            partition_key=partition_key,
            freshness_overdue=True,
            infrastructure_healthy=infrastructure_healthy,
            missed_schedule_eligible=missed_schedule_auto_replay_eligible(
                partition_key,
                now_utc,
            ),
        )
        decision = decide_recovery(runtime_state.observation)
        summaries.append(f"{partition_key}:{decision.reason_code}")

        if decision.action in {
            RecoveryAction.ALERT_AND_WAIT,
            RecoveryAction.ALERT_MANUAL,
        }:
            context.log.warning(
                "Recovery partition=%s action=%s reason=%s missing_marts=%s",
                partition_key,
                decision.action.value,
                decision.reason_code,
                ",".join(runtime_state.missing_mart_asset_keys) or "none",
            )
        if not decision.should_auto_replay:
            continue

        attempt = runtime_state.observation.auto_replay_attempts + 1
        return dg.RunRequest(
            run_key=recovery_run_key(partition_key, attempt),
            partition_key=partition_key,
            tags={
                "commerce/automation": "recovery-sensor",
                RECOVERY_TAG: AUTO_RECOVERY_TAG_VALUE,
                RECOVERY_ATTEMPT_TAG: str(attempt),
                RECOVERY_REASON_TAG: decision.reason_code,
            },
        )

    return dg.SkipReason("No replay-safe partition found: " + "; ".join(summaries))
