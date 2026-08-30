"""Phase 6D 的受治理 Operational Incident Drilldown。

只在诊断指向 DATA_PIPELINE_SUSPECTED 时读取结构化 Dagster incident evidence；不会执行恢复。
"""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import yaml

from agent.semantic_query import SemanticQuerySpec

from .contracts import IncidentDrilldownResult, IncidentDrilldownStatus
from .provider import DagsterIncidentRuntimeProvider, IncidentRuntimeProvider


class GovernedOperationalIncidentDrilldown:
    """把 DiagnosticResult 与 IncidentRuntimeProvider 组合成受治理的分区故障证据。"""
    def __init__(self, project_root: Path | str, *, runtime_provider: IncidentRuntimeProvider | None = None):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/operational_incident_policy.yml").read_text(encoding="utf-8")
        )
        self.runtime_provider = runtime_provider or DagsterIncidentRuntimeProvider(self.root)

    def execute(self, spec: SemanticQuerySpec) -> IncidentDrilldownResult:
        """对需要排查的业务分区读取 failed run / recovery policy / completeness 等证据；无运行证据时保持 DEFERRED。"""
        start = datetime.fromisoformat(spec.start_time.replace("Z", "+00:00")).astimezone(timezone.utc).date()
        end = datetime.fromisoformat(spec.end_time.replace("Z", "+00:00")).astimezone(timezone.utc).date()
        days = (end - start).days + 1
        if days < 1 or days > int(self.policy["limits"]["max_partitions_per_drilldown"]):
            return IncidentDrilldownResult(
                status=IncidentDrilldownStatus.BLOCKED,
                evidence="STATIC_CONTRACT",
                warnings=[f"Incident drilldown supports at most {self.policy['limits']['max_partitions_per_drilldown']} daily partitions per request."],
                validation="INCIDENT_PARTITION_LIMIT_EXCEEDED",
            )
        result = self.runtime_provider.inspect(spec)
        if result.status not in {
            IncidentDrilldownStatus.COMPLETE,
            IncidentDrilldownStatus.NO_INCIDENT,
        }:
            return result

        # Runtime evidence may expose structured Phase 3C cause tags. Absence stays UNKNOWN.
        for partition in result.partitions:
            failed = partition.latest_failed_run
            if failed and failed.failure_class not in {
                "none",
                "transient_runtime",
                "infrastructure_unavailable",
                "deterministic_code",
                "data_contract",
                "unknown",
            }:
                return IncidentDrilldownResult(
                    status=IncidentDrilldownStatus.BLOCKED,
                    evidence="RUNTIME_VERIFIED",
                    partitions=result.partitions,
                    warnings=[f"Unrecognized structured failure class: {failed.failure_class}"],
                    validation="UNRECOGNIZED_FAILURE_CLASS",
                )
        return result
