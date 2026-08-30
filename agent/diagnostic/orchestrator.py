"""Phase 6C 的受治理诊断状态机。

链路：Semantic Query → Anomaly → Operational Health Gate → 可选 Driver Attribution。
工程边界：数据管道不健康或健康状态未知时，禁止把异常升级为业务驱动结论。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from agent.anomaly_analysis import (
    AnomalyState,
    GovernedAnomalyDetector,
    SignalCauseClass,
)
from agent.driver_attribution import DriverAttributionStatus, GovernedDriverAttribution
from agent.incident_drilldown import GovernedOperationalIncidentDrilldown
from agent.incident_response import GovernedIncidentResponsePlanner
from agent.semantic_query import SemanticQueryStatus

from .contracts import DiagnosticRequestPlan, DiagnosticResult, DiagnosticStatus, DiagnosticTraceStep
from .operational_health import DagsterPartitionCompletenessHealthProvider, OperationalHealthProvider


class GovernedDiagnosticOrchestrator:
    """协调异常检测、Dagster 当前分区健康与 Driver Attribution，输出一个可审计 DiagnosticResult。"""
    def __init__(
        self,
        project_root: Path | str,
        *,
        anomaly_detector=None,
        driver_attribution=None,
        health_provider: OperationalHealthProvider | None = None,
        incident_drilldown=None,
        incident_response_planner=None,
    ):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/diagnostic_orchestrator_policy.yml").read_text(encoding="utf-8")
        )
        self.anomaly_detector = anomaly_detector or GovernedAnomalyDetector(self.root)
        self.driver_attribution = driver_attribution or GovernedDriverAttribution(self.root)
        self.health_provider = health_provider or DagsterPartitionCompletenessHealthProvider(self.root)
        self.incident_drilldown = incident_drilldown or GovernedOperationalIncidentDrilldown(self.root)
        self.incident_response_planner = incident_response_planner or GovernedIncidentResponsePlanner(self.root)

    def execute(self, plan: DiagnosticRequestPlan) -> DiagnosticResult:
        """执行诊断状态机。
        
        NORMAL 时停止；UNHEALTHY 时标记 DATA_PIPELINE_SUSPECTED；UNKNOWN/DEFERRED 时 UNRESOLVED；只有 HEALTHY + RUNTIME_VERIFIED anomaly 才进入 6B。
        """
        if plan.status is not SemanticQueryStatus.READY or plan.spec is None:
            return DiagnosticResult(
                status=self._map_semantic_status(plan.status),
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=list(plan.warnings),
                validation="DIAGNOSTIC_PLAN_NOT_READY",
            )

        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            return DiagnosticResult(
                status=DiagnosticStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=[f"Diagnostic execution is disabled; set {gate}=true only in the intended runtime environment."],
                validation="NOT_EXECUTED",
            )

        health = self.health_provider.snapshot(plan.spec)
        trace = [
            DiagnosticTraceStep(
                stage="OPERATIONAL_HEALTH",
                status=health.state.value,
                evidence=health.evidence,
                detail=health.details,
            )
        ]

        anomaly_plan = self.anomaly_detector.plan(plan.spec, question=plan.question)
        trace.append(
            DiagnosticTraceStep(
                stage="ANOMALY_PLAN",
                status=anomaly_plan.status.value,
                evidence="STATIC_CONTRACT",
            )
        )
        anomaly = self.anomaly_detector.detect(anomaly_plan, operational_health=health)
        trace.append(
            DiagnosticTraceStep(
                stage="ANOMALY_DETECTION",
                status=anomaly.status.value,
                evidence=anomaly.evidence,
                detail=f"state={anomaly.anomaly_state.value}; cause={anomaly.cause_class.value}",
            )
        )

        if anomaly.status is not SemanticQueryStatus.COMPLETE:
            return DiagnosticResult(
                status=self._map_semantic_status(anomaly.status),
                evidence=anomaly.evidence,
                plan=plan,
                operational_health=health,
                anomaly=anomaly,
                trace=trace,
                warnings=list(dict.fromkeys(anomaly.warnings)),
                validation="ANOMALY_NOT_COMPLETE",
            )

        if anomaly.anomaly_state is AnomalyState.NORMAL:
            return DiagnosticResult(
                status=DiagnosticStatus.NORMAL,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                operational_health=health,
                anomaly=anomaly,
                trace=trace,
                warnings=list(dict.fromkeys(anomaly.warnings)),
                validation="NO_ANOMALY_DRIVER_STAGE_SKIPPED",
            )

        if anomaly.cause_class is SignalCauseClass.DATA_PIPELINE_SUSPECTED:
            trace.append(
                DiagnosticTraceStep(
                    stage="DRIVER_ATTRIBUTION",
                    status="SKIPPED",
                    evidence="RUNTIME_VERIFIED",
                    detail="Business-driver attribution is intentionally blocked while operational health is unhealthy.",
                )
            )
            incident = self.incident_drilldown.execute(plan.spec)
            trace.append(
                DiagnosticTraceStep(
                    stage="OPERATIONAL_INCIDENT_DRILLDOWN",
                    status=incident.status.value,
                    evidence=incident.evidence,
                    detail=incident.validation,
                )
            )
            incident_response = self.incident_response_planner.plan(incident)
            trace.append(
                DiagnosticTraceStep(
                    stage="INCIDENT_RESPONSE_PLANNING",
                    status=incident_response.status.value,
                    evidence=incident_response.evidence,
                    detail=incident_response.validation,
                )
            )
            return DiagnosticResult(
                status=DiagnosticStatus.DATA_PIPELINE_SUSPECTED,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                operational_health=health,
                anomaly=anomaly,
                incident=incident,
                incident_response=incident_response,
                trace=trace,
                warnings=list(dict.fromkeys([
                    *anomaly.warnings,
                    *incident.warnings,
                    *incident_response.warnings,
                    "Business-driver attribution was not executed because the exact-partition operational-health gate is unhealthy.",
                ])),
                validation="PIPELINE_SUSPECTED_INCIDENT_DRILLDOWN_ATTEMPTED",
            )

        if anomaly.cause_class is not SignalCauseClass.BUSINESS_SIGNAL_SUSPECTED:
            trace.append(
                DiagnosticTraceStep(
                    stage="DRIVER_ATTRIBUTION",
                    status="SKIPPED",
                    evidence=health.evidence,
                    detail="Operational health is not sufficiently verified for business-driver attribution.",
                )
            )
            return DiagnosticResult(
                status=DiagnosticStatus.UNRESOLVED,
                evidence=anomaly.evidence,
                plan=plan,
                operational_health=health,
                anomaly=anomaly,
                trace=trace,
                warnings=list(dict.fromkeys([
                    *anomaly.warnings,
                    "Business-vs-pipeline cause remains unresolved because operational health is not RUNTIME_VERIFIED HEALTHY.",
                ])),
                validation="CAUSE_UNRESOLVED_DRIVER_STAGE_BLOCKED",
            )

        attribution = self.driver_attribution.execute(anomaly)
        trace.append(
            DiagnosticTraceStep(
                stage="DRIVER_ATTRIBUTION",
                status=attribution.status.value,
                evidence=attribution.evidence,
                detail=attribution.validation,
            )
        )
        if attribution.status is DriverAttributionStatus.COMPLETE:
            status = DiagnosticStatus.BUSINESS_DRIVERS_IDENTIFIED
            validation = "BUSINESS_SIGNAL_WITH_VERIFIED_DRIVER_LENSES"
        elif attribution.status is DriverAttributionStatus.PARTIAL:
            status = DiagnosticStatus.PARTIAL
            validation = "BUSINESS_SIGNAL_WITH_PARTIAL_DRIVER_LENSES"
        elif attribution.status is DriverAttributionStatus.DEFERRED:
            status = DiagnosticStatus.PARTIAL
            validation = "BUSINESS_SIGNAL_DRIVER_ATTRIBUTION_DEFERRED"
        elif attribution.status is DriverAttributionStatus.BLOCKED:
            status = DiagnosticStatus.PARTIAL
            validation = "BUSINESS_SIGNAL_DRIVER_ATTRIBUTION_BLOCKED"
        else:
            status = DiagnosticStatus.PARTIAL
            validation = "BUSINESS_SIGNAL_DRIVER_ATTRIBUTION_FAILED"

        evidence = "RUNTIME_VERIFIED" if anomaly.evidence == "RUNTIME_VERIFIED" else anomaly.evidence
        return DiagnosticResult(
            status=status,
            evidence=evidence,
            plan=plan,
            operational_health=health,
            anomaly=anomaly,
            attribution=attribution,
            trace=trace,
            warnings=list(dict.fromkeys([*anomaly.warnings, *attribution.warnings])),
            validation=validation,
        )

    @staticmethod
    def _map_semantic_status(status: SemanticQueryStatus) -> DiagnosticStatus:
        """把 Semantic Query 状态映射到 DiagnosticStatus，并保持 BLOCKED/DEFERRED 等证据边界。"""
        return {
            SemanticQueryStatus.DEFERRED: DiagnosticStatus.DEFERRED,
            SemanticQueryStatus.BLOCKED: DiagnosticStatus.BLOCKED,
            SemanticQueryStatus.ERROR: DiagnosticStatus.ERROR,
            SemanticQueryStatus.CLARIFICATION_REQUIRED: DiagnosticStatus.BLOCKED,
            SemanticQueryStatus.READY: DiagnosticStatus.READY,
            SemanticQueryStatus.COMPLETE: DiagnosticStatus.PARTIAL,
        }[status]
