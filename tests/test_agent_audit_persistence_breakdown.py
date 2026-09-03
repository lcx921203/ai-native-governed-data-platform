"""Audit Persistence Breakdown + Group Commit Efficiency 的确定性契约测试。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace

import yaml

import agent.audit.writer as writer_module
from agent.api.timing import APITimingTrace
from agent.audit.contracts import AgentAuditRecord
from agent.audit.writer import _ProcessDurableAuditSink
from agent.observability.contracts import CostSummary, RunTrace


ROOT = Path(__file__).resolve().parents[1]


def _record(
    trace_id: str,
    *,
    event_type: str = "RUNTIME",
) -> AgentAuditRecord:
    """构造只含结构化元数据的最小 Audit Record。"""

    return AgentAuditRecord(
        schema_version=1,
        occurred_at="2026-09-03T00:00:00+00:00",
        trace_id=trace_id,
        tenant_id="audit-breakdown-tenant",
        subject="audit-breakdown-user",
        intent="METRIC_DEFINITION",
        route_status="PLANNED",
        target_kind="metric",
        target_id="activity_net_sales",
        authorization_status="PASS",
        runtime_status="ANSWERED",
        answer_validated=True,
        stage_statuses=("router:PLANNED",),
        duration_ms=1.0,
        estimated_context_tokens=0,
        tool_result_count=1,
        analysis_unit_attempts=0,
        retry_rounds=0,
        llm_calls=0,
        llm_total_tokens=0,
        llm_models=(),
        provider_cost_usd=None,
        cost_per_answer_usd=None,
        monetary_cost_known=False,
        event_type=event_type,
    )


def test_mixed_event_batch_receipt_reports_physical_sync_composition(
    monkeypatch,
    tmp_path,
):
    """一个 Durable Batch 必须能证明它到底合并了多少种 Audit Event。"""

    path = tmp_path / "audit.jsonl"
    sync_calls = 0
    sync_lock = Lock()

    def fake_sync(_fd: int) -> None:
        """只统计物理 Sync 次数。"""

        nonlocal sync_calls
        with sync_lock:
            sync_calls += 1

    monkeypatch.setattr(
        writer_module,
        "_durable_sync_fd",
        fake_sync,
    )
    monkeypatch.setattr(
        writer_module,
        "_fsync_parent_directory",
        lambda _path: None,
    )

    sink = _ProcessDurableAuditSink(
        path,
        file_mode=0o600,
        durable_sync=True,
        group_commit_window_ms=25.0,
    )
    events = (
        "RUNTIME",
        "RUNTIME",
        "RUNTIME",
        "API_TIMING",
    )
    barrier = Barrier(len(events))

    def append_one(index: int):
        """让 4 条不同类型 Record 进入同一 Group Commit Window。"""

        barrier.wait()
        return sink.append(
            (
                json.dumps(
                    {"index": index},
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
            event_type=events[index],
        )

    try:
        with ThreadPoolExecutor(
            max_workers=len(events)
        ) as pool:
            results = list(
                pool.map(
                    append_one,
                    range(len(events)),
                )
            )

        batches = [
            item["batch"]
            for item in results
        ]
        assert sync_calls == 1
        assert sink.sync_count == 1
        assert {
            item.batch_id
            for item in batches
        } == {1}
        assert {
            item.total_records
            for item in batches
        } == {4}
        assert {
            item.runtime_records
            for item in batches
        } == {3}
        assert {
            item.api_timing_records
            for item in batches
        } == {1}
        assert all(
            item.sync_ms >= 0.0
            and item.coalesce_ms >= 0.0
            for item in batches
        )
    finally:
        sink.close()


def test_api_timing_numeric_metrics_are_bounded_and_not_additive_phases():
    """Batch ID / Sync Duration 必须走 Numeric Metric，不得混入 Phase Sum。"""

    trace = APITimingTrace(
        allowed_phases=frozenset(
            {
                "runtime.audit.durability_wait",
            }
        ),
        allowed_metrics=frozenset(
            {
                "runtime.audit.batch_id",
                "runtime.audit.batch_sync_ms",
            }
        ),
    )
    trace.add(
        "runtime.audit.durability_wait",
        4.0,
    )
    trace.add_metric(
        "runtime.audit.batch_id",
        7,
    )
    trace.add_metric(
        "runtime.audit.batch_sync_ms",
        2.5,
    )

    assert dict(trace.as_tuple()) == {
        "runtime.audit.durability_wait": 4.0,
    }
    assert dict(trace.metrics_tuple()) == {
        "runtime.audit.batch_id": 7.0,
        "runtime.audit.batch_sync_ms": 2.5,
    }


def test_run_trace_does_not_serialize_internal_audit_receipt():
    """Audit Receipt 可以向 API Timing 传递，但不能进入 Runtime/Public Serialization。"""

    cost = CostSummary(
        total_duration_ms=1.0,
        estimated_context_tokens=0,
        tool_result_count=0,
        analysis_unit_attempts=0,
        retry_rounds=0,
    )
    trace = RunTrace(
        trace_id="trace-internal",
        tenant_id="tenant",
        subject="subject",
        status="ANSWERED",
        answer_validated=True,
        stages=(),
        cost=cost,
        audit_status="WRITTEN",
        audit_persistence=SimpleNamespace(
            batch_id=9,
            batch_sync_ms=2.0,
        ),
    )

    payload = trace.to_dict()
    assert "audit_persistence" not in payload

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
    )
    assert "batch_id" not in serialized
    assert "batch_sync_ms" not in serialized


def test_e2e_evidence_aggregates_batch_ids_without_exporting_raw_values():
    """E2E 可以按 Batch ID 去重，但最终 JSON Evidence 不能包含原始 ID。"""

    text = (
        ROOT
        / "acceptance/agent_slo/api_e2e_load.py"
    ).read_text(
        encoding="utf-8"
    )

    for expected in (
        "audit_runtime_batch_ids",
        "set(audit_runtime_batch_ids)",
        '"unique_sync_batches"',
        '"runtime_records_per_sync"',
        '"grouped_runtime_record_fraction"',
        '"batch_sync_latency_ms"',
        '"batch_coalesce_latency_ms"',
        '"runtime_audit_receipt_coverage"',
        "Raw group commit batch IDs must never appear",
    ):
        assert expected in text

    assert (
        '"runtime.audit.batch_id":'
        not in text
    )


def test_v6_policies_keep_breakdown_diagnostic_and_production_slo_uncalibrated():
    """增加 Breakdown Evidence 不能自动把 GitHub Runner 结果晋升成生产 SLO。"""

    audit_policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_audit_policy.yml"
        ).read_text(
            encoding="utf-8"
        )
    )
    timing_policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_api_timing_policy.yml"
        ).read_text(
            encoding="utf-8"
        )
    )
    slo_policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(
            encoding="utf-8"
        )
    )

    assert audit_policy["version"] == 5
    assert timing_policy["version"] == 2
    assert slo_policy["version"] == 8
    assert (
        slo_policy["evidence"][
            "schema_version"
        ]
        == 4
    )
    assert (
        slo_policy["principles"][
            "group_commit_efficiency_is_diagnostic_not_latency_gate"
        ]
        is True
    )
    assert (
        slo_policy["principles"][
            "single_github_runner_run_cannot_select_final_group_commit_window"
        ]
        is True
    )
    assert (
        slo_policy["promotion"][
            "current_production_slo_status"
        ]
        == "UNCALIBRATED"
    )
