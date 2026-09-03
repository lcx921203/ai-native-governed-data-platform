"""Agent API Outside-Runtime Phase Timing 的确定性契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.api.timing import (
    APITimingTrace,
    GovernedAPITimingAuditor,
)
from agent.tenancy import RequestContext


ROOT = Path(__file__).resolve().parents[1]


def test_api_timing_trace_accepts_only_governed_phase_labels():
    """Timing Label 必须是 Policy 固定集合，不能混入 Prompt/Tool/Payload 文本。"""

    trace = APITimingTrace(
        allowed_phases=frozenset(
            {
                "auth.jwt_verification",
                "admission.shared_guard",
            }
        ),
        allowed_metrics=frozenset(
            {
                "runtime.audit.batch_sync_ms",
            }
        ),
    )
    trace.add(
        "auth.jwt_verification",
        1.25,
    )
    trace.add(
        "auth.jwt_verification",
        0.75,
    )

    assert dict(trace.as_tuple()) == {
        "auth.jwt_verification": 2.0,
    }

    trace.add_metric(
        "runtime.audit.batch_sync_ms",
        0.8,
    )
    assert dict(trace.metrics_tuple()) == {
        "runtime.audit.batch_sync_ms": 0.8,
    }

    with pytest.raises(ValueError):
        trace.add(
            "prompt:activity_net_sales 是什么意思？",
            1.0,
        )
    with pytest.raises(ValueError):
        trace.add_metric(
            "runtime.audit.batch_id:raw-user-text",
            1.0,
        )


def test_api_timing_audit_record_contains_only_structured_phase_duration(monkeypatch):
    """API_TIMING Record 只保存固定阶段和数字，不保存自由文本业务内容。"""

    monkeypatch.setenv(
        "AGENT_API_PHASE_TIMING_MODE",
        "disabled",
    )
    auditor = GovernedAPITimingAuditor(
        ROOT
    )
    request_context = RequestContext(
        tenant_id="timing-test-tenant",
        subject="timing-test-subject",
        scopes=frozenset(
            {"commerce:semantic:read"}
        ),
    )
    record = auditor.build_record(
        trace_id="trace-1",
        request_context=request_context,
        http_status=200,
        server_total_ms=12.5,
        phase_timings=(
            ("auth.jwt_verification", 1.2),
            ("runtime.audit.durability_wait", 8.3),
        ),
        numeric_metrics=(
            ("runtime.audit.batch_sync_ms", 2.1),
        ),
    ).to_dict()

    assert record["event_type"] == "API_TIMING"
    assert record["runtime_status"] == "HTTP_200"
    assert record["stage_timings"] == [
        {
            "stage": "auth.jwt_verification",
            "duration_ms": 1.2,
        },
        {
            "stage": "runtime.audit.durability_wait",
            "duration_ms": 8.3,
        },
    ]
    assert record["numeric_metrics"] == [
        {
            "metric": "runtime.audit.batch_sync_ms",
            "value": 2.1,
        }
    ]
    assert "question" not in record
    assert "answer" not in record
    assert "token" not in record


def test_production_api_instruments_outside_runtime_phases_without_public_timing_fields():
    """Production API Source 必须测量固定 Phase，但不写 Public Timing Header/Body。"""

    text = (
        ROOT
        / "agent/api/main.py"
    ).read_text(encoding="utf-8")

    for expected in (
        "GovernedAPITimingMiddleware",
        '"auth.jwt_verification"',
        '"auth.request_context_mapping"',
        '"admission.shared_guard"',
        '"threadpool.queue_wait"',
        '"runtime.observer_non_audit"',
        '"runtime.audit.serialize"',
        '"runtime.audit.append_lock_wait"',
        '"runtime.audit.append"',
        '"runtime.audit.durability_wait"',
        '"runtime.audit.writer_residual"',
        '"runtime.audit.batch_sync_ms"',
        '"lease.release"',
        '"endpoint.response_build"',
        "_run_runtime_with_api_timing",
    ):
        assert expected in text

    assert "X-Agent-Timing" not in text
    assert "Server-Timing" not in text


def test_e2e_evidence_correlates_api_timing_and_runtime_without_raw_trace_ids():
    """E2E 必须聚合 API_TIMING，并保留 Server/Runtime/Client 三层残差语义。"""

    text = (
        ROOT
        / "acceptance/agent_slo/api_e2e_load.py"
    ).read_text(encoding="utf-8")

    for expected in (
        '"AGENT_API_PHASE_TIMING_MODE": "audit"',
        'event_type == "API_TIMING"',
        '"api_server_total_latency_ms"',
        '"api_phase_latency_ms"',
        '"api_server_unattributed_latency_ms"',
        '"client_after_server_residual_latency_ms"',
        '"api_timing_coverage"',
        '"schema_version": 4',
        '"audit_persistence"',
        '"runtime_audit_receipt_coverage"',
        '"unique_sync_batches"',
        '"runtime_records_per_sync"',
        '"grouped_runtime_record_fraction"',
        "Raw trace IDs must never appear",
    ):
        assert expected in text


def test_outside_runtime_timing_policy_is_diagnostic_not_production_slo_authority():
    """Phase Breakdown 只能用于定位，不得把 GitHub Runner 延迟自动晋升生产 SLO。"""

    timing_policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_api_timing_policy.yml"
        ).read_text(encoding="utf-8")
    )
    slo_policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )

    assert timing_policy["version"] == 2
    assert (
        timing_policy["runtime"]["default_mode"]
        == "disabled"
    )
    assert (
        timing_policy["principles"][
            "public_response_has_no_timing_header_or_body"
        ]
        is True
    )
    assert (
        timing_policy["principles"][
            "runtime_post_observer_parent_is_replaced_by_additive_children"
        ]
        is True
    )
    assert (
        timing_policy["principles"][
            "group_commit_batch_metrics_are_non_additive_diagnostics"
        ]
        is True
    )
    assert (
        timing_policy["calibration"][
            "production_slo_authority"
        ]
        is False
    )

    assert slo_policy["version"] == 8
    assert slo_policy["evidence"]["schema_version"] == 4
    assert (
        slo_policy["principles"][
            "api_outside_runtime_phase_latency_is_recorded"
        ]
        is True
    )
    assert (
        slo_policy["principles"][
            "audit_persistence_breakdown_is_recorded"
        ]
        is True
    )
    assert (
        slo_policy["principles"][
            "raw_group_commit_batch_ids_are_not_uploaded"
        ]
        is True
    )
    assert (
        slo_policy["promotion"][
            "current_production_slo_status"
        ]
        == "UNCALIBRATED"
    )
