from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from agent.anomaly_analysis import (
    GovernedAnomalyDetector,
    OperationalHealthSnapshot,
    OperationalHealthState,
)
from agent.diagnostic import (
    DiagnosticEvidenceComposer,
    DiagnosticStatus,
    GovernedDiagnosticOrchestrator,
    GovernedDiagnosticPlanner,
)
from agent.driver_attribution import GovernedDriverAttribution
from agent.incident_drilldown import IncidentDrilldownResult, IncidentDrilldownStatus
from agent.response import Claim, ClaimKind, ResponseEnvelope, AnswerStatus, AnswerDraft, render_deterministic, validate_answer_draft
from agent.semantic_query import SemanticQueryPlan, SemanticQueryResult, SemanticQueryStatus

ROOT = Path(__file__).resolve().parents[1]


class FixedHealthProvider:
    def __init__(self, state=OperationalHealthState.HEALTHY, evidence="RUNTIME_VERIFIED", details="phase6c-test"):
        self.value = OperationalHealthSnapshot(state=state, evidence=evidence, details=details)
        self.calls = []

    def snapshot(self, spec):
        self.calls.append(spec)
        return self.value


class FixedIncidentDrilldown:
    def __init__(self, result=None):
        self.result = result or IncidentDrilldownResult(
            status=IncidentDrilldownStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            validation="PHASE6C_INCIDENT_TEST",
        )
        self.calls = []

    def execute(self, spec):
        self.calls.append(spec)
        return self.result


class SequenceExecutor:
    def __init__(self, values, evidence="RUNTIME_VERIFIED"):
        self.values = list(values)
        self.evidence = evidence
        self.calls = []

    def execute(self, plan):
        self.calls.append(plan)
        metric = plan.spec.metric_names[0]
        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence=self.evidence,
            plan=plan,
            rows=[{metric: str(self.values.pop(0))}],
            columns=[metric],
            validation="FAKE_DIAGNOSTIC_ANOMALY_RUNTIME",
        )


class LensExecutor:
    def __init__(self, payloads, fail_dimensions=()):
        self.payloads = payloads
        self.fail_dimensions = set(fail_dimensions)
        self.calls = []

    def execute(self, plan: SemanticQueryPlan):
        self.calls.append(plan)
        dimension = plan.spec.group_by[0]
        if dimension in self.fail_dimensions:
            return SemanticQueryResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                warnings=["MetricFlow Explain rejected this driver lens."],
                validation="FAKE_EXPLAIN_REJECTED",
            )
        key = "current" if plan.spec.start_time.startswith("2026-08-05") else "reference"
        rows = self.payloads[dimension][key]
        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            rows=rows,
            columns=[dimension, plan.spec.metric_names[0]],
            validation="FAKE_DIAGNOSTIC_DRIVER_RUNTIME",
        )


def down_payloads():
    return {
        "store__region": {
            "current": [{"store__region": "West", "gross_sales": "20"}, {"store__region": "South", "gross_sales": "30"}],
            "reference": [{"store__region": "West", "gross_sales": "60"}, {"store__region": "South", "gross_sales": "40"}],
        },
        "item__brand": {
            "current": [{"item__brand": "Coca-Cola", "gross_sales": "20"}, {"item__brand": "Generic", "gross_sales": "30"}],
            "reference": [{"item__brand": "Coca-Cola", "gross_sales": "50"}, {"item__brand": "Generic", "gross_sales": "50"}],
        },
        "item__category": {
            "current": [{"item__category": "Beverage", "gross_sales": "10"}, {"item__category": "Snack", "gross_sales": "40"}],
            "reference": [{"item__category": "Beverage", "gross_sales": "60"}, {"item__category": "Snack", "gross_sales": "40"}],
        },
    }


def build_orchestrator(*, anomaly_values, health_provider, fail_dimensions=(), incident_drilldown=None):
    anomaly_executor = SequenceExecutor(anomaly_values)
    driver_executor = LensExecutor(down_payloads(), fail_dimensions=fail_dimensions)
    orchestrator = GovernedDiagnosticOrchestrator(
        ROOT,
        anomaly_detector=GovernedAnomalyDetector(ROOT, executor=anomaly_executor),
        driver_attribution=GovernedDriverAttribution(ROOT, executor=driver_executor),
        health_provider=health_provider,
        incident_drilldown=incident_drilldown or FixedIncidentDrilldown(),
    )
    return orchestrator, anomaly_executor, driver_executor


def plan(question="为什么 2026-08-05 Gross Sales 跌了这么多？"):
    return GovernedDiagnosticPlanner(ROOT).plan(question)


def enable_runtime(monkeypatch):
    monkeypatch.setenv("PHASE6C_ALLOW_DIAGNOSTIC", "true")
    monkeypatch.setenv("PHASE6A_ALLOW_ANOMALY_QUERY", "true")
    monkeypatch.setenv("PHASE6B_ALLOW_DRIVER_ATTRIBUTION", "true")
    monkeypatch.setenv("PHASE5B_ALLOW_METRICFLOW_QUERY", "true")


def test_policy_requires_anomaly_health_driver_evidence_chain():
    policy = yaml.safe_load((ROOT / "agent/contracts/diagnostic_orchestrator_policy.yml").read_text())
    assert policy["principles"]["operational_health_uses_exact_partition_current_truth"] is True
    assert policy["principles"]["latest_run_status_is_not_operational_health_truth"] is True
    assert policy["principles"]["unhealthy_pipeline_blocks_business_driver_attribution"] is True
    assert policy["principles"]["cross_lens_contributions_must_not_be_summed"] is True
    assert policy["principles"]["claim_ledger_required_before_llm_rendering"] is True
    assert policy["principles"]["arbitrary_sql"] is False


def test_natural_language_planner_resolves_today_to_explicit_utc_date():
    planner = GovernedDiagnosticPlanner(
        ROOT,
        now_provider=lambda: datetime(2026, 8, 18, 23, 45, tzinfo=timezone.utc),
    )
    result = planner.plan("为什么今天 Gross Sales 跌了这么多？")
    assert result.status is SemanticQueryStatus.READY
    assert result.metric == "gross_sales"
    assert result.spec.start_time == "2026-08-18T00:00:00Z"
    assert result.spec.end_time == "2026-08-18T23:59:59Z"
    assert result.relative_time_resolution == "today->2026-08-18 UTC"


def test_planner_requires_exactly_one_metric_and_blocks_sql():
    planner = GovernedDiagnosticPlanner(ROOT)
    multiple = planner.plan("为什么 2026-08-05 Gross Sales 和 AOV 都跌了？")
    assert multiple.status is SemanticQueryStatus.CLARIFICATION_REQUIRED
    sql = planner.plan("2026-08-05 Gross Sales select * from orders")
    assert sql.status is SemanticQueryStatus.BLOCKED


def test_phase6c_gate_defers_before_health_or_metricflow_calls(monkeypatch):
    health = FixedHealthProvider()
    orchestrator, anomaly_executor, driver_executor = build_orchestrator(
        anomaly_values=[50, 90, 100, 110, 95, 105, 100, 102],
        health_provider=health,
    )
    monkeypatch.setenv("PHASE6C_ALLOW_DIAGNOSTIC", "false")
    result = orchestrator.execute(plan())
    assert result.status is DiagnosticStatus.DEFERRED
    assert result.validation == "NOT_EXECUTED"
    assert health.calls == []
    assert anomaly_executor.calls == []
    assert driver_executor.calls == []


def test_normal_signal_stops_before_driver_attribution(monkeypatch):
    enable_runtime(monkeypatch)
    health = FixedHealthProvider()
    orchestrator, _, driver_executor = build_orchestrator(
        anomaly_values=[95, 100, 100, 100, 100, 100, 100, 100],
        health_provider=health,
    )
    result = orchestrator.execute(plan())
    assert result.status is DiagnosticStatus.NORMAL
    assert result.attribution is None
    assert driver_executor.calls == []


def test_unhealthy_pipeline_stops_before_business_driver_attribution(monkeypatch):
    enable_runtime(monkeypatch)
    health = FixedHealthProvider(OperationalHealthState.UNHEALTHY, details="2026-08-05 missing=orders")
    orchestrator, _, driver_executor = build_orchestrator(
        anomaly_values=[50, 90, 100, 110, 95, 105, 100, 102],
        health_provider=health,
    )
    result = orchestrator.execute(plan())
    assert result.status is DiagnosticStatus.DATA_PIPELINE_SUSPECTED
    assert result.attribution is None
    assert driver_executor.calls == []
    assert any(step.stage == "DRIVER_ATTRIBUTION" and step.status == "SKIPPED" for step in result.trace)
    assert any(step.stage == "OPERATIONAL_INCIDENT_DRILLDOWN" for step in result.trace)
    assert result.incident is not None

    envelope = DiagnosticEvidenceComposer(ROOT).compose(result)
    assert any(c.kind is ClaimKind.OPERATIONAL_HEALTH for c in envelope.claims)
    assert not any(c.kind is ClaimKind.DRIVER_ATTRIBUTION for c in envelope.claims)
    assert any("intentionally stopped" in item for item in envelope.limitations)


def test_healthy_critical_signal_runs_driver_attribution_and_builds_claim_ledger(monkeypatch):
    enable_runtime(monkeypatch)
    orchestrator, _, driver_executor = build_orchestrator(
        anomaly_values=[50, 90, 100, 110, 95, 105, 100, 102],
        health_provider=FixedHealthProvider(),
    )
    result = orchestrator.execute(plan())
    assert result.status is DiagnosticStatus.BUSINESS_DRIVERS_IDENTIFIED
    assert len(driver_executor.calls) == 6
    assert result.attribution is not None

    envelope = DiagnosticEvidenceComposer(ROOT).compose(result)
    kinds = [claim.kind for claim in envelope.claims]
    assert ClaimKind.ANOMALY_OBSERVATION in kinds
    assert ClaimKind.OPERATIONAL_HEALTH in kinds
    assert ClaimKind.DIAGNOSTIC_CLASSIFICATION in kinds
    assert kinds.count(ClaimKind.DRIVER_ATTRIBUTION) == 3
    driver_text = " ".join(c.text for c in envelope.claims if c.kind is ClaimKind.DRIVER_ATTRIBUTION)
    assert "West" in driver_text
    assert "Coca-Cola" in driver_text
    assert "Beverage" in driver_text
    assert not any("combined_contribution" in c.text for c in envelope.claims)
    assert any("must not be added across lenses" in item for item in envelope.limitations)

    draft = render_deterministic(envelope)
    assert validate_answer_draft(envelope, draft) is True
    assert "BUSINESS_SIGNAL_SUSPECTED" in draft.answer


def test_unknown_operational_health_preserves_observed_anomaly_but_blocks_driver(monkeypatch):
    enable_runtime(monkeypatch)
    health = FixedHealthProvider(OperationalHealthState.UNKNOWN, evidence="DEFERRED", details="Dagster unavailable")
    orchestrator, _, driver_executor = build_orchestrator(
        anomaly_values=[50, 90, 100, 110, 95, 105, 100, 102],
        health_provider=health,
    )
    result = orchestrator.execute(plan())
    assert result.status is DiagnosticStatus.UNRESOLVED
    assert result.anomaly.evidence == "RUNTIME_VERIFIED"
    assert result.attribution is None
    assert driver_executor.calls == []
    envelope = DiagnosticEvidenceComposer(ROOT).compose(result)
    assert any(c.kind is ClaimKind.ANOMALY_OBSERVATION for c in envelope.claims)
    assert not any(c.kind is ClaimKind.OPERATIONAL_HEALTH for c in envelope.claims)
    assert any("Dagster unavailable" in item for item in envelope.limitations)


def test_partial_driver_lens_preserves_verified_lenses(monkeypatch):
    enable_runtime(monkeypatch)
    orchestrator, _, _ = build_orchestrator(
        anomaly_values=[50, 90, 100, 110, 95, 105, 100, 102],
        health_provider=FixedHealthProvider(),
        fail_dimensions={"item__brand"},
    )
    result = orchestrator.execute(plan())
    assert result.status is DiagnosticStatus.PARTIAL
    envelope = DiagnosticEvidenceComposer(ROOT).compose(result)
    driver_claims = [c for c in envelope.claims if c.kind is ClaimKind.DRIVER_ATTRIBUTION]
    assert len(driver_claims) == 2
    assert any("did not complete" in item for item in envelope.limitations)


def test_runtime_claim_with_static_evidence_is_rejected_by_answer_validator():
    envelope = ResponseEnvelope(
        question="q",
        intent="DIAGNOSTIC_QUERY",
        status=AnswerStatus.ANSWERED,
        claims=[
            Claim(
                "C01",
                ClaimKind.ANOMALY_OBSERVATION,
                "fake runtime fact",
                evidence="STATIC_CONTRACT",
                runtime_observed=True,
            )
        ],
    )
    draft = AnswerDraft(answer="fake", used_claim_ids=("C01",))
    with pytest.raises(ValueError, match="RUNTIME_VERIFIED"):
        validate_answer_draft(envelope, draft)
