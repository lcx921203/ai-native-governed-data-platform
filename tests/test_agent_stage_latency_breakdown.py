"""Agent Runtime Stage Latency Breakdown（阶段耗时拆解）契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent.runtime import GovernedAgentRuntime
from agent.runtime.contracts import RuntimeStage
from agent.tenancy import RequestContext


ROOT = Path(__file__).resolve().parents[1]
QUESTION = "activity_net_sales 是什么意思？"


def _context() -> RequestContext:
    """构造 E2E Stage Breakdown 使用的最小可信 Metric Context。"""

    return RequestContext(
        tenant_id="stage-test-tenant",
        subject="stage-test-user",
        scopes=frozenset({"commerce:semantic:read"}),
        allowed_metrics=frozenset({"activity_net_sales"}),
    )


def test_runtime_stage_contract_serializes_non_negative_duration():
    """Stage Trace 新字段必须向后兼容，并只输出非负数值。"""

    old_style = RuntimeStage("router", "PLANNED", "METRIC_DEFINITION")
    assert old_style.duration_ms == 0.0
    assert old_style.to_dict()["duration_ms"] == 0.0

    measured = RuntimeStage(
        "executor",
        "COMPLETE",
        "tool_plan",
        duration_ms=12.34567,
    )
    assert measured.to_dict()["duration_ms"] == 12.346


def test_real_deterministic_runtime_records_stage_durations(monkeypatch):
    """真实本地 Runtime Path 必须给 Router/Context/Executor/Renderer/Validator 计时。"""

    monkeypatch.setenv("AGENT_AUDIT_MODE", "disabled")
    runtime = GovernedAgentRuntime(ROOT)
    result = runtime.run(
        QUESTION,
        _context(),
    )

    assert result.answer_validated is True
    by_stage = {
        item.stage: item.duration_ms
        for item in result.stage_trace
    }
    for stage in (
        "router",
        "authorization",
        "context_planner",
        "context_loader",
        "executor",
        "claim_ledger",
        "renderer",
        "answer_validator",
    ):
        assert stage in by_stage
        assert by_stage[stage] >= 0.0

    measured_sum = sum(by_stage.values())
    assert result.observability.cost.total_duration_ms + 1.0 >= measured_sum


def test_runtime_audit_decomposes_context_loader_and_executor_without_double_count(
    monkeypatch,
    tmp_path,
):
    """Audit 用 Child + Residual 替换父阶段，避免 Parent/Child 双计时。"""

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_AUDIT_MODE", "jsonl")
    monkeypatch.setenv("AGENT_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("AGENT_AUDIT_FAILURE_MODE", "fail_closed")

    runtime = GovernedAgentRuntime(ROOT)
    result = runtime.run(
        QUESTION,
        _context(),
    )
    assert result.observability.audit_status == "WRITTEN"

    rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    row = next(item for item in rows if item["event_type"] == "RUNTIME")

    assert row["stage_timings"]
    stage_names = {
        item["stage"]
        for item in row["stage_timings"]
    }

    assert {"router", "renderer"}.issubset(stage_names)
    assert any(
        name.startswith("context_loader.")
        for name in stage_names
    )
    assert any(
        name.startswith("executor.")
        for name in stage_names
    )

    # 有 Substage 时父阶段不再重复写入 Timing；状态仍保留在 stage_statuses。
    assert "context_loader" not in stage_names
    assert "executor" not in stage_names
    assert "context_loader:READY" in row["stage_statuses"]
    assert "executor:COMPLETE" in row["stage_statuses"]

    assert all(
        set(item) == {"stage", "duration_ms"}
        and item["duration_ms"] >= 0
        for item in row["stage_timings"]
    )

    # Child + Residual 应保持可加和，不得因为分解而超过 Runtime Total。
    timing_sum = sum(
        item["duration_ms"]
        for item in row["stage_timings"]
    )
    assert row["duration_ms"] + 2.0 >= timing_sum

    serialized = json.dumps(row, ensure_ascii=False)
    assert QUESTION not in serialized
    assert "estimated_tokens=" not in serialized
    assert "tool_plan" not in serialized


def test_e2e_load_correlates_runtime_audit_without_uploading_raw_trace_or_audit():
    """E2E Evidence 只保留聚合 Stage/Substage Percentile 和 Coverage。"""

    text = (
        ROOT
        / "acceptance/agent_slo/api_e2e_load.py"
    ).read_text(encoding="utf-8")

    assert '"AGENT_AUDIT_MODE": "jsonl"' in text
    assert '"AGENT_AUDIT_FAILURE_MODE": "fail_closed"' in text
    assert "TemporaryDirectory" in text
    assert '"runtime_stage_latency_ms"' in text
    assert '"runtime_total_latency_ms"' in text
    assert '"http_outside_runtime_latency_ms"' in text
    assert '"runtime_unattributed_latency_ms"' in text
    assert '"stage_timing_coverage"' in text
    assert '"raw_audit_uploaded": False' in text
    assert "Raw trace IDs must never appear" in text


def test_stage_breakdown_policy_remains_lab_evidence_not_production_slo():
    """子阶段拆解帮助定位瓶颈，但不能自动晋升生产 SLO。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )

    assert policy["version"] == 6
    assert (
        policy["principles"][
            "api_e2e_records_internal_runtime_stage_latency"
        ]
        is True
    )
    assert (
        policy["principles"][
            "context_loader_substage_latency_is_recorded"
        ]
        is True
    )
    assert (
        policy["principles"][
            "executor_substage_latency_is_recorded"
        ]
        is True
    )
    assert (
        policy["principles"][
            "decomposed_parent_is_replaced_by_children_plus_residual"
        ]
        is True
    )
    assert (
        policy["principles"][
            "public_agent_api_does_not_expose_stage_timing"
        ]
        is True
    )
    assert (
        policy["promotion"]["current_production_slo_status"]
        == "UNCALIBRATED"
    )
