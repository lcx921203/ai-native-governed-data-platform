"""把 Dagster 当前 9-Mart exact-partition truth 投影成 Agent Operational Health。

业务逻辑：对语义查询覆盖的每个业务日分区读取 Dagster 精确分区物化事实，
而不是用“最近一次 Run 是否 SUCCESS”代替数据完整性。

Dagster API：运行时通过 ``DagsterInstance`` 与 Chapter 04 的
``collect_partition_recovery_state`` 读取同一套 exact-partition truth。

工程边界：本模块只读，不触发 Recovery；Dagster Runtime 不可用时返回
``UNKNOWN / DEFERRED``，绝不伪造 HEALTHY。
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from agent.anomaly_analysis import OperationalHealthSnapshot, OperationalHealthState
from agent.semantic_query import SemanticQuerySpec


class OperationalHealthProvider(Protocol):
    """Operational Health 的只读 Provider 接口。

    输入：一个已经受治理的 ``SemanticQuerySpec``。
    输出：``OperationalHealthSnapshot``。
    工程边界：Provider 只能读取健康事实，不能触发 Dagster Retry / Replay。
    """

    def snapshot(self, spec: SemanticQuerySpec) -> OperationalHealthSnapshot:
        """读取语义查询时间窗口对应的运行健康快照。"""
        ...


class DeferredOperationalHealthProvider:
    """Runtime 未连接时使用的 Fail-Closed Provider。

    它不会因为静态源码存在就声称运行健康，而是固定返回 ``UNKNOWN / DEFERRED``。
    """

    def snapshot(self, spec: SemanticQuerySpec) -> OperationalHealthSnapshot:
        """返回明确的 UNKNOWN / DEFERRED 快照，不访问真实 Dagster Runtime。"""
        return OperationalHealthSnapshot(
            state=OperationalHealthState.UNKNOWN,
            evidence="DEFERRED",
            source="dagster_exact_partition_completeness",
            details="Real Dagster exact-partition completeness evidence is not available in this runtime.",
        )


class DagsterPartitionCompletenessHealthProvider:
    """从 Dagster event/run storage 读取当前 exact-partition completeness。

    输入：项目根目录，可选注入 DagsterInstance 与 ``now_provider`` 便于测试。
    输出：查询窗口整体的 HEALTHY / UNHEALTHY / UNKNOWN 快照。
    数据语义：判断复用 Chapter 04 当前 9-Mart 精确分区真值。
    工程边界：这里只读，不拥有 Recovery 执行权。
    """

    def __init__(self, project_root: Path | str, *, instance=None, now_provider=None):
        self.root = Path(project_root).resolve()
        self.instance = instance
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def snapshot(self, spec: SemanticQuerySpec) -> OperationalHealthSnapshot:
        """逐日检查语义查询窗口内的 exact partition completeness。

        逻辑：
        1. 延迟导入 Dagster，保证静态/手机环境没有 Dagster 也能 import；
        2. 把 semantic query 时间范围映射为日分区；
        3. 对每个分区读取 Chapter 04 的 RecoveryRuntimeState；
        4. 逾期且缺 Mart → UNHEALTHY；未到 freshness deadline 但不完整 → UNKNOWN；
           全部分区 9/9 完整 → HEALTHY。

        工程边界：任何 Runtime 模块、Instance、时间映射或状态读取失败都返回 DEFERRED，
        不把异常吞掉后继续做业务归因。
        """
        try:
            import dagster as dg  # type: ignore
            from orchestration.dagster.commerce_dagster.automation_policy import (
                missed_schedule_auto_replay_eligible,
                partition_deadline_utc,
            )
            from orchestration.dagster.commerce_dagster.recovery_state_current import (
                collect_partition_recovery_state,
            )
        except Exception as exc:
            return self._deferred(f"Dagster runtime modules are unavailable: {exc}")

        try:
            instance = self.instance or dg.DagsterInstance.get()
        except Exception as exc:
            return self._deferred(f"Dagster instance is unavailable: {exc}")

        try:
            start = self._parse(spec.start_time).date()
            end = self._parse(spec.end_time).date()
        except Exception as exc:
            return self._deferred(f"Cannot map semantic-query time window to Dagster daily partitions: {exc}")

        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        incomplete_overdue = []
        incomplete_not_due = []
        inspected = []
        current = start
        try:
            while current <= end:
                key = current.isoformat()
                overdue = partition_deadline_utc(key) <= now
                state = collect_partition_recovery_state(
                    instance,
                    partition_key=key,
                    freshness_overdue=overdue,
                    infrastructure_healthy=True,
                    missed_schedule_eligible=missed_schedule_auto_replay_eligible(key, now),
                )
                inspected.append(key)
                if state.missing_mart_asset_keys:
                    item = f"{key}:missing={','.join(state.missing_mart_asset_keys)}"
                    (incomplete_overdue if overdue else incomplete_not_due).append(item)
                current += timedelta(days=1)
        except Exception as exc:
            return self._deferred(f"Dagster exact-partition state read failed: {exc}")

        if incomplete_overdue:
            return OperationalHealthSnapshot(
                state=OperationalHealthState.UNHEALTHY,
                evidence="RUNTIME_VERIFIED",
                source="dagster_exact_partition_completeness",
                details="Overdue incomplete partition(s): " + "; ".join(incomplete_overdue),
            )
        if incomplete_not_due:
            return OperationalHealthSnapshot(
                state=OperationalHealthState.UNKNOWN,
                evidence="RUNTIME_VERIFIED",
                source="dagster_exact_partition_completeness",
                details="Queried partition(s) are not complete but their freshness deadline has not passed: "
                + "; ".join(incomplete_not_due),
            )
        return OperationalHealthSnapshot(
            state=OperationalHealthState.HEALTHY,
            evidence="RUNTIME_VERIFIED",
            source="dagster_exact_partition_completeness",
            details="All queried exact daily partitions are complete: " + ", ".join(inspected),
        )

    @staticmethod
    def _parse(value: str) -> datetime:
        """把 ISO 时间转换成 UTC ``datetime``，供逐日分区映射使用。"""
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

    @staticmethod
    def _deferred(detail: str) -> OperationalHealthSnapshot:
        """构造 Dagster Runtime 不可用时的 UNKNOWN / DEFERRED 健康快照。"""
        return OperationalHealthSnapshot(
            state=OperationalHealthState.UNKNOWN,
            evidence="DEFERRED",
            source="dagster_exact_partition_completeness",
            details=detail,
        )
