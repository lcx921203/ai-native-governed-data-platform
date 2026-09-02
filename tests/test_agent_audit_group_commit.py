"""Agent Audit Durable Group Commit（持久化组提交）契约测试。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock

import pytest
import yaml

import agent.audit.writer as writer_module
from agent.audit import AuditWriteError, GovernedAuditWriter
from agent.audit.contracts import AgentAuditRecord
from agent.audit.writer import _ProcessDurableAuditSink


ROOT = Path(__file__).resolve().parents[1]


def _record(trace_id: str) -> AgentAuditRecord:
    """构造不含自由文本业务内容的最小 Audit Record。"""

    return AgentAuditRecord(
        schema_version=1,
        occurred_at="2026-09-03T00:00:00+00:00",
        trace_id=trace_id,
        tenant_id="audit-group-test",
        subject="user",
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
    )


def test_concurrent_records_share_one_durable_sync(monkeypatch, tmp_path):
    """Coalesce Window 内的并发 Record 应共享一次 durable sync。"""

    path = tmp_path / "audit.jsonl"
    sync_calls = 0
    sync_lock = Lock()

    def fake_sync(_fd: int) -> None:
        """只统计 Sync 次数；并发聚合行为由 Sink 自身负责。"""

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
        # 测试使用较大 Window 保证 8 个线程确定性进入同一 Batch；
        # 生产 Policy 默认仍只有 1ms。
        group_commit_window_ms=25.0,
    )

    barrier = Barrier(8)

    def append_one(index: int):
        """让 8 个线程尽量同时进入第一轮 Group Commit。"""

        barrier.wait()
        return sink.append(
            (
                json.dumps(
                    {"index": index},
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )

    try:
        with ThreadPoolExecutor(
            max_workers=8
        ) as pool:
            receipts = list(
                pool.map(
                    append_one,
                    range(8),
                )
            )

        rows = [
            json.loads(line)
            for line in path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]

        assert len(rows) == 8
        assert {
            row["index"]
            for row in rows
        } == set(range(8))

        assert sync_calls == 1
        assert sink.sync_count == 1
        assert sink.append_count == 8
        assert all(
            receipt.durable
            for receipt in receipts
        )
        assert {
            receipt.generation
            for receipt in receipts
        } == set(range(1, 9))
    finally:
        sink.close()


def test_durable_sync_failure_fails_closed_and_poison_sink(monkeypatch, tmp_path):
    """一次 durable sync 失败后，当前和后续 ACK 都不能伪装成功。"""

    path = tmp_path / "audit.jsonl"

    monkeypatch.setattr(
        writer_module,
        "_fsync_parent_directory",
        lambda _path: None,
    )

    def fail_sync(_fd: int) -> None:
        raise OSError("simulated durable sync failure")

    monkeypatch.setattr(
        writer_module,
        "_durable_sync_fd",
        fail_sync,
    )

    sink = _ProcessDurableAuditSink(
        path,
        file_mode=0o600,
        durable_sync=True,
        group_commit_window_ms=0.0,
    )

    try:
        with pytest.raises(AuditWriteError):
            sink.append(b'{"trace":"one"}\n')

        with pytest.raises(AuditWriteError):
            sink.append(b'{"trace":"two"}\n')
    finally:
        sink.close()


def test_writer_instances_share_process_sink_and_keep_0600(monkeypatch, tmp_path):
    """Runtime / Guard / Timing 的多个 Writer 应共享同一个进程 Sink。"""

    audit_path = tmp_path / "audit.jsonl"

    monkeypatch.setenv(
        "AGENT_AUDIT_MODE",
        "jsonl",
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_PATH",
        str(audit_path),
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_FAILURE_MODE",
        "fail_closed",
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_GROUP_COMMIT_WINDOW_MS",
        "0",
    )

    first = GovernedAuditWriter(ROOT)
    second = GovernedAuditWriter(ROOT)

    first_receipt = first.write(
        _record("trace-1")
    )
    second_receipt = second.write(
        _record("trace-2")
    )

    assert first_receipt is not None
    assert second_receipt is not None
    assert first_receipt.durable is True
    assert second_receipt.durable is True

    assert first._sink is second._sink
    assert first._sink is not None

    rows = [
        json.loads(line)
        for line in audit_path.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert [
        row["trace_id"]
        for row in rows
    ] == [
        "trace-1",
        "trace-2",
    ]

    # 只检查 permission bits，不依赖 tmp parent 的其他 mode。
    assert (
        audit_path.stat().st_mode
        & 0o777
    ) == 0o600


def test_group_commit_window_is_governed_and_bounded(monkeypatch, tmp_path):
    """配置错误不能让请求无限等待以追求更大 Batch。"""

    monkeypatch.setenv(
        "AGENT_AUDIT_MODE",
        "jsonl",
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_PATH",
        str(tmp_path / "audit.jsonl"),
    )
    monkeypatch.setenv(
        "AGENT_AUDIT_GROUP_COMMIT_WINDOW_MS",
        "500",
    )

    with pytest.raises(
        AuditWriteError
    ):
        GovernedAuditWriter(ROOT)


def test_audit_policy_preserves_durable_fail_closed_semantics():
    """性能优化不能退化成 fire-and-forget Audit。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_audit_policy.yml"
        ).read_text(
            encoding="utf-8"
        )
    )

    assert policy["version"] == 4

    principles = policy[
        "principles"
    ]
    for key in (
        "acknowledged_runtime_audit_is_durable",
        "asynchronous_fire_and_forget_is_not_used_for_fail_closed_runtime_audit",
        "concurrent_records_can_share_one_durable_sync",
        "durable_group_commit_preserves_per_record_ack",
        "durable_sync_failure_fails_all_waiting_records",
        "process_reuses_one_append_fd_per_audit_path",
        "first_file_creation_fsyncs_parent_directory",
        "rotation_requires_copytruncate_or_process_restart",
    ):
        assert principles[key] is True

    storage = policy[
        "storage"
    ]
    assert (
        storage[
            "durability_mode"
        ]
        == "synchronous_group_commit"
    )
    assert (
        storage[
            "durable_sync_primitive"
        ]
        == "fdatasync_or_fsync"
    )
    assert (
        storage[
            "acknowledged_record_requires_durable_sync"
        ]
        is True
    )
