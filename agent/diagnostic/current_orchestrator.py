"""Post-baseline diagnostic composition wired to the current nine-Mart Recovery State Reader.

Frozen Phase 6 diagnostic classes stay byte-for-byte unchanged.  This composition layer
injects the post-baseline providers so current CLI/runtime diagnostics include
``order_lifecycle_snapshot`` in exact-partition completeness without rewriting Phase 6 history.
"""

from __future__ import annotations

from pathlib import Path

from agent.incident_drilldown.drilldown import GovernedOperationalIncidentDrilldown
from agent.incident_drilldown.provider_current import DagsterIncidentRuntimeProvider

from .operational_health_current import DagsterPartitionCompletenessHealthProvider
from .orchestrator import GovernedDiagnosticOrchestrator


def build_current_diagnostic_orchestrator(project_root: Path | str) -> GovernedDiagnosticOrchestrator:
    """构造当前诊断编排器，并显式注入九张 Mart 的运行真值 Provider。

    输入项目根目录；输出仍是冻结的 ``GovernedDiagnosticOrchestrator`` 类型，
    但 Operational Health 与 Incident Drilldown 都读取 ``recovery_state_current``。
    这样扩展当前能力时不修改 Phase 6 frozen source。
    """

    root = Path(project_root).resolve()
    health = DagsterPartitionCompletenessHealthProvider(root)
    incident = GovernedOperationalIncidentDrilldown(
        root,
        runtime_provider=DagsterIncidentRuntimeProvider(root),
    )
    return GovernedDiagnosticOrchestrator(
        root,
        health_provider=health,
        incident_drilldown=incident,
    )
