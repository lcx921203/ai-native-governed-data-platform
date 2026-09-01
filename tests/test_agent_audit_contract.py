"""Agent Runtime Audit 的隐私、持久化与 Fail-Closed 契约测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.audit import (
    AuditWriteError,
    GovernedAuditReader,
    GovernedAuditWriter,
)
from agent.observability import GovernedRunObserver
from agent.router import DeterministicToolRouter
from agent.runtime.contracts import (
    AgentRunResult,
    AgentRuntimeStatus,
    RuntimeStage,
)
from agent.tenancy import RequestContext


ROOT = Path(__file__).resolve().parents[1]


def _context() -> RequestContext:
    """构造一个最小可信测试租户。"""

    return RequestContext(
        tenant_id="tenant-west",
        subject="user-1",
        scopes=frozenset({"commerce:semantic:read"}),
        allowed_metrics=frozenset({"gross_sales"}),
    )


def _result() -> AgentRunResult:
    """构造一个带 Route 的已验证测试结果。"""

    result = AgentRunResult(
        question="这是不应该被写进 Audit 的原始问题",
        status=AgentRuntimeStatus.ANSWERED,
        route=DeterministicToolRouter(ROOT).plan(
            "gross_sales 的定义是什么？"
        ),
        draft=SimpleNamespace(answer="这个答案也不能进入 Audit"),
        answer_validated=True,
        stage_trace=(
            RuntimeStage("router", "PLANNED"),
            RuntimeStage("authorization", "PASS"),
            RuntimeStage("answer_validator", "PASS"),
        ),
    )
    return result


def test_jsonl_audit_contains_structured_metadata_but_not_prompt_answer_or_token(
    monkeypatch,
    tmp_path,
):
    """Audit Record 只能保存结构化元数据，不能保存敏感自由文本。"""

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_AUDIT_MODE", "jsonl")
    monkeypatch.setenv("AGENT_AUDIT_PATH", str(audit_path))
    monkeypatch.setenv("AGENT_AUDIT_FAILURE_MODE", "fail_closed")

    observer = GovernedRunObserver(ROOT)
    result = observer.attach(
        _result(),
        _context(),
        total_duration_ms=12.5,
    )

    assert result.observability.audit_status == "WRITTEN"
    row = json.loads(audit_path.read_text(encoding="utf-8").strip())

    assert row["tenant_id"] == "tenant-west"
    assert row["subject"] == "user-1"
    assert row["intent"] == "METRIC_DEFINITION"
    assert row["authorization_status"] == "PASS"
    assert row["runtime_status"] == "ANSWERED"
    assert row["trace_id"] == result.observability.trace_id

    serialized = json.dumps(row, ensure_ascii=False)
    assert "这是不应该被写进 Audit 的原始问题" not in serialized
    assert "这个答案也不能进入 Audit" not in serialized
    assert "Bearer" not in serialized
    assert "token" not in serialized.lower()


def test_audit_reader_filters_by_tenant_and_trace(monkeypatch, tmp_path):
    """Reader 只能按受控结构化字段查询，并保持结果上限。"""

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AGENT_AUDIT_MODE", "jsonl")
    monkeypatch.setenv("AGENT_AUDIT_PATH", str(audit_path))

    observer = GovernedRunObserver(ROOT)
    first = observer.attach(
        _result(),
        _context(),
        total_duration_ms=1.0,
    )
    second = observer.attach(
        _result(),
        RequestContext(
            tenant_id="tenant-south",
            subject="user-2",
            scopes=frozenset({"commerce:semantic:read"}),
            allowed_metrics=frozenset({"gross_sales"}),
        ),
        total_duration_ms=2.0,
    )

    reader = GovernedAuditReader(ROOT)
    west = reader.query(tenant_id="tenant-west")
    assert len(west) == 1
    assert west[0]["trace_id"] == first.observability.trace_id

    exact = reader.query(
        trace_id=second.observability.trace_id,
        max_results=1,
    )
    assert len(exact) == 1
    assert exact[0]["tenant_id"] == "tenant-south"


class _FailingAuditWriter:
    """测试 Audit 持久化失败时是否真正 Fail Closed。"""

    enabled = True
    fail_closed = True

    def write(self, _record):
        """模拟底层磁盘/挂载不可写。"""

        raise AuditWriteError("disk unavailable")


def test_audit_failure_withholds_answer_in_fail_closed_mode():
    """Audit 写失败时不能仍然向生产 API 返回已验证答案。"""

    observer = GovernedRunObserver(
        ROOT,
        audit_writer=_FailingAuditWriter(),
    )
    result = observer.attach(
        _result(),
        _context(),
        total_duration_ms=3.0,
    )

    assert result.status is AgentRuntimeStatus.ERROR
    assert result.answer_validated is False
    assert result.draft is None
    assert result.observability.audit_status == "FAILED"
    assert any(
        "Audit persistence failed" in warning
        for warning in result.warnings
    )


def test_library_default_keeps_audit_disabled(monkeypatch):
    """普通单元测试/本地工具不会因为未配置生产 Audit 而产生文件。"""

    monkeypatch.delenv("AGENT_AUDIT_MODE", raising=False)
    writer = GovernedAuditWriter(ROOT)

    assert writer.enabled is False
