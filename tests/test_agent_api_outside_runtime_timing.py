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
        )
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

    with pytest.raises(ValueError):
        trace.add(
            "prompt:activity_net_sales 是什么意思？",
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
            ("runtime.post_observer_audit", 8.3),
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
            "stage": "runtime.post_observer_audit",
            "duration_ms": 8.3,
        },
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
        '"runtime.post_observer_audit"',
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
        '"schema_version": 3',
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

    assert timing_policy["version"] == 1
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
        timing_policy["calibration"][
            "production_slo_authority"
        ]
        is False
    )

    assert slo_policy["version"] == 5
    assert slo_policy["evidence"]["schema_version"] == 3
    assert (
        slo_policy["principles"][
            "api_outside_runtime_phase_latency_is_recorded"
        ]
        is True
    )
    assert (
        slo_policy["promotion"][
            "current_production_slo_status"
        ]
        == "UNCALIBRATED"
    )
