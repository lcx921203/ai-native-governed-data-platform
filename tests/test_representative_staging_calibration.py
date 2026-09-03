"""Representative Staging Calibration Runner V1 的确定性契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from acceptance.agent_slo.representative_staging_calibration import (
    HTTPResult,
    WorkloadRequest,
    EVIDENCE_KIND,
    build_representative_staging_evidence,
    load_representative_staging_calibration_policy,
    run_representative_staging_calibration,
    validate_representative_staging_plan,
)
from acceptance.agent_slo.staging_evidence_manifest import (
    load_staging_evidence_policy,
    validate_staging_evidence_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    """构造 Manifest V1 的最小代表性 Staging 清单。"""

    return {
        "schema_version": 1,
        "evidence_kind": "REPRESENTATIVE_STAGING_EVIDENCE_MANIFEST_V1",
        "environment": {
            "label": "representative-staging-shared-redis",
            "deployment_id": "staging-release-20260903-02",
            "captured_at": "2026-09-03T10:30:00+00:00",
            "git_sha": "b" * 40,
        },
        "topology": {
            "api_replicas": 2,
            "workers_per_replica": 1,
            "traffic_backend": "redis",
            "shared_capacity_budget": True,
            "persistent_audit_storage": True,
            "audit_fail_closed": True,
        },
        "dependencies": {
            "llm_renderer": "live",
            "metricflow": "live",
            "datahub": "live",
            "knowledge_rag": "live",
            "dagster_runtime_evidence": "live",
            "trino": "not_in_agent_dynamic_path",
        },
        "workload": {
            "intent_request_counts": {
                "METRIC_QUERY": 40,
                "METADATA_LOOKUP": 20,
                "KNOWLEDGE": 20,
                "ANALYSIS": 20,
            },
            "timeout_probe_passed": True,
            "admission_saturation_probe_passed": True,
        },
        "tenancy": {
            "tenant_count": 2,
            "subject_count": 4,
            "cross_tenant_isolation_probe_passed": True,
        },
        "audit": {
            "group_commit_window_ms": 5.0,
            "runtime_audit_receipt_coverage": 1.0,
        },
    }


def _plan() -> dict:
    """构造无占位符、无 Token 明文的私有运行计划。"""

    return {
        "schema_version": 1,
        "plan_kind": "REPRESENTATIVE_STAGING_CALIBRATION_PLAN_V1",
        "endpoint": {
            "base_url_env": "STAGING_URL",
            "audit_path_env": "STAGING_AUDIT_PATH",
            "workers": 8,
            "request_timeout_seconds": 30,
        },
        "identities": [
            {
                "id": "a1",
                "tenant_id": "tenant-a",
                "subject": "subject-a1",
                "bearer_token_env": "TOKEN_A1",
            },
            {
                "id": "a2",
                "tenant_id": "tenant-a",
                "subject": "subject-a2",
                "bearer_token_env": "TOKEN_A2",
            },
            {
                "id": "b1",
                "tenant_id": "tenant-b",
                "subject": "subject-b1",
                "bearer_token_env": "TOKEN_B1",
            },
            {
                "id": "b2",
                "tenant_id": "tenant-b",
                "subject": "subject-b2",
                "bearer_token_env": "TOKEN_B2",
            },
        ],
        "workloads": {
            "METRIC_QUERY": [{"id": "metric", "question": "metric question"}],
            "METADATA_LOOKUP": [{"id": "metadata", "question": "metadata question"}],
            "KNOWLEDGE": [{"id": "knowledge", "question": "knowledge question"}],
            "ANALYSIS": [{"id": "analysis", "question": "analysis question"}],
        },
        "probes": {
            "timeout": {"identity": "a1", "question": "timeout probe"},
            "admission_saturation": {
                "identity": "a1",
                "question": "admission probe",
                "concurrent_requests": 4,
            },
            "cross_tenant_isolation": {
                "identity": "a1",
                "target_tenant_id": "tenant-b",
                "question": "cross tenant probe",
            },
        },
    }


def _env() -> dict[str, str]:
    """测试只存放假的环境变量值；最终 Evidence 必须完全去敏。"""

    return {
        "STAGING_URL": "https://staging.example.invalid",
        "STAGING_AUDIT_PATH": "/tmp/staging-audit.jsonl",
        "TOKEN_A1": "token-a1-secret",
        "TOKEN_A2": "token-a2-secret",
        "TOKEN_B1": "token-b1-secret",
        "TOKEN_B2": "token-b2-secret",
    }


def _manifest_validation() -> dict:
    """用真实 Manifest Validator 生成去敏校验结果。"""

    return validate_staging_evidence_manifest(
        _manifest(),
        policy=load_staging_evidence_policy(ROOT),
    )


def test_plan_maps_manifest_logical_names_to_real_runtime_intents():
    """Manifest 的逻辑类别不必伪装成 Runtime Enum；Runner 策略显式维护映射。"""

    policy = load_representative_staging_calibration_policy(ROOT)
    validation = validate_representative_staging_plan(
        _plan(),
        manifest_validation=_manifest_validation(),
        policy=policy,
        environment=_env(),
    )

    assert validation["valid"] is True
    mapping = policy["workload"]["logical_intent_runtime_mapping"]
    assert mapping["METADATA_LOOKUP"] == [
        "METADATA_DISCOVERY",
        "ENTITY_CONTEXT",
        "DATASET_GOVERNANCE",
        "LINEAGE_QUERY",
    ]
    assert mapping["KNOWLEDGE"] == ["KNOWLEDGE_QUERY"]


def test_plan_rejects_secret_fields_placeholders_and_same_tenant_probe():
    """运行计划不能包含 Token 明文、未替换模板或伪造的同租户隔离 Probe。"""

    plan = _plan()
    plan["credentials"] = {"bearer_token": "secret"}
    plan["workloads"]["METRIC_QUERY"][0]["question"] = "${QUESTION}"
    plan["probes"]["cross_tenant_isolation"]["target_tenant_id"] = "tenant-a"
    validation = validate_representative_staging_plan(
        plan,
        manifest_validation=_manifest_validation(),
        policy=load_representative_staging_calibration_policy(ROOT),
        environment=_env(),
    )

    assert validation["valid"] is False
    assert set(validation["errors"]) >= {
        "FORBIDDEN_SECRET_FIELD_PRESENT",
        "UNRESOLVED_PLAN_PLACEHOLDER",
        "CROSS_TENANT_TARGET_MUST_DIFFER",
    }


def test_runner_executes_exact_manifest_mix_and_emits_privacy_safe_evidence(tmp_path):
    """100 个混合请求必须真实对齐 HTTP + Runtime Audit + Persistence Receipt。"""

    manifest_path = tmp_path / "manifest.yml"
    plan_path = tmp_path / "plan.yml"
    output_path = tmp_path / "evidence.json"
    manifest_path.write_text(yaml.safe_dump(_manifest(), allow_unicode=True), encoding="utf-8")
    plan_path.write_text(yaml.safe_dump(_plan(), allow_unicode=True), encoding="utf-8")

    token_identity = {
        "token-a1-secret": ("tenant-a", "subject-a1"),
        "token-a2-secret": ("tenant-a", "subject-a2"),
        "token-b1-secret": ("tenant-b", "subject-b1"),
        "token-b2-secret": ("tenant-b", "subject-b2"),
    }
    question_intent = {
        "metric question": "METRIC_QUERY",
        "metadata question": "LINEAGE_QUERY",
        "knowledge question": "KNOWLEDGE_QUERY",
        "analysis question": "ANALYSIS",
    }
    trace_context: dict[str, tuple[str, str, str]] = {}
    sequence = {"value": 0}
    admission_sequence = {"value": 0}

    def transport(**kwargs) -> HTTPResult:
        question = kwargs["question"]
        if question == "timeout probe":
            return HTTPResult(
                504,
                {"detail": {"code": "AGENT_REQUEST_TIMEOUT", "trace_id": "probe-timeout"}},
                9.0,
                "probe-timeout",
            )
        if question == "cross tenant probe":
            return HTTPResult(
                403,
                {"detail": {"code": "AGENT_AUTHORIZATION_DENIED", "trace_id": "probe-denied"}},
                3.0,
                "probe-denied",
            )
        if question == "admission probe":
            admission_sequence["value"] += 1
            if admission_sequence["value"] == 1:
                return HTTPResult(
                    429,
                    {"detail": {"code": "TENANT_CONCURRENCY_LIMIT", "trace_id": "probe-429"}},
                    1.0,
                    "probe-429",
                )
            return HTTPResult(
                200,
                {"status": "COMPLETE", "answer": "not persisted", "answer_validated": True, "trace_id": f"probe-ok-{admission_sequence['value']}"},
                2.0,
                f"probe-ok-{admission_sequence['value']}",
            )

        sequence["value"] += 1
        trace_id = f"trace-{sequence['value']}"
        tenant_id, subject = token_identity[kwargs["token"]]
        trace_context[trace_id] = (tenant_id, subject, question_intent[question])
        return HTTPResult(
            200,
            {
                "status": "COMPLETE",
                "answer": "sensitive answer",
                "answer_validated": True,
                "trace_id": trace_id,
            },
            5.0,
            trace_id,
        )

    def audit_loader(_path: Path, trace_ids: set[str]):
        rows = {}
        for index, trace_id in enumerate(sorted(trace_ids), start=1):
            tenant_id, subject, intent = trace_context[trace_id]
            batch_id = (index - 1) // 4 + 1
            rows[trace_id] = {
                "RUNTIME": {
                    "event_type": "RUNTIME",
                    "trace_id": trace_id,
                    "tenant_id": tenant_id,
                    "subject": subject,
                    "intent": intent,
                    "runtime_status": "COMPLETE",
                    "answer_validated": True,
                    "duration_ms": 4.0,
                    "tool_result_count": 1,
                    "llm_calls": 1,
                    "llm_total_tokens": 100,
                    "monetary_cost_known": True,
                    "provider_cost_usd": 0.001,
                },
                "API_TIMING": {
                    "event_type": "API_TIMING",
                    "trace_id": trace_id,
                    "stage_timings": [
                        {"stage": "runtime.audit.durability_wait", "duration_ms": 1.2}
                    ],
                    "numeric_metrics": [
                        {"metric": "runtime.audit.batch_id", "value": batch_id},
                        {"metric": "runtime.audit.batch_runtime_records", "value": 4},
                        {"metric": "runtime.audit.batch_sync_ms", "value": 0.8},
                    ],
                },
            }
        return rows

    report = run_representative_staging_calibration(
        ROOT,
        manifest_path=manifest_path,
        plan_path=plan_path,
        output_path=output_path,
        transport=transport,
        audit_loader=audit_loader,
        environment=_env(),
    )

    assert report["evidence_kind"] == EVIDENCE_KIND
    assert report["calibration_status"] == "REPRESENTATIVE_STAGING_PASS"
    assert report["workload"]["request_count"] == 100
    assert report["workload"]["logical_intent_request_counts"] == {
        "ANALYSIS": 20,
        "KNOWLEDGE": 20,
        "METADATA_LOOKUP": 20,
        "METRIC_QUERY": 40,
    }
    assert report["audit"]["runtime_audit_coverage"] == 1.0
    assert report["audit"]["persistence_receipt_coverage"] == 1.0
    assert report["audit"]["runtime_records_per_sync"] == 4.0
    assert report["audit"]["grouped_runtime_record_fraction"] == 1.0
    assert report["workload"]["live_llm_call_coverage"] == 1.0
    assert report["workload"]["tool_result_coverage"] == 1.0
    assert report["probes"]["timeout"]["passed"] is True
    assert report["probes"]["admission_saturation"]["passed"] is True
    assert report["probes"]["cross_tenant_isolation"]["passed"] is True
    assert report["promotion"]["automatic_production_promotion"] is False

    serialized = output_path.read_text(encoding="utf-8")
    for secret in (
        "metric question",
        "metadata question",
        "knowledge question",
        "analysis question",
        "sensitive answer",
        "token-a1-secret",
        "staging.example.invalid",
        "/tmp/staging-audit.jsonl",
        "trace-",
    ):
        assert secret not in serialized


def test_missing_live_llm_or_persistence_receipt_fails_closed():
    """HTTP 200 不是成功证据；Live LLM 或持久化回执缺失都必须失败。"""

    request = WorkloadRequest(
        logical_intent="METRIC_QUERY",
        case_id="metric",
        question="private question",
        identity_id="a1",
        tenant_id="tenant-a",
        subject="subject-a1",
        token="private-token",
        expected_runtime_intents=("METRIC_QUERY",),
    )
    result = HTTPResult(
        200,
        {"status": "COMPLETE", "answer_validated": True, "trace_id": "trace-private"},
        5.0,
        "trace-private",
    )
    report = build_representative_staging_evidence(
        manifest_validation={
            **_manifest_validation(),
            "valid": True,
        },
        manifest=_manifest(),
        plan_validation={
            "valid": True,
            "tenant_count": 2,
            "subject_count": 4,
        },
        workload_requests=[request],
        workload_results=[result],
        audit_rows={
            "trace-private": {
                "RUNTIME": {
                    "event_type": "RUNTIME",
                    "tenant_id": "tenant-a",
                    "subject": "subject-a1",
                    "intent": "METRIC_QUERY",
                    "answer_validated": True,
                    "duration_ms": 4.0,
                    "tool_result_count": 1,
                    "llm_calls": 0,
                    "llm_total_tokens": 0,
                    "monetary_cost_known": False,
                },
                "API_TIMING": {
                    "event_type": "API_TIMING",
                    "stage_timings": [],
                    "numeric_metrics": [],
                },
            }
        },
        probe_results={
            "timeout": {"passed": True},
            "admission_saturation": {"passed": True},
            "cross_tenant_isolation": {"passed": True},
        },
        policy=load_representative_staging_calibration_policy(ROOT),
        generated_at="2026-09-03T10:40:00+00:00",
    )

    assert report["calibration_status"] == "REPRESENTATIVE_STAGING_FAILED"
    assert report["workload"]["live_llm_call_coverage"] == 0.0
    assert report["audit"]["persistence_receipt_coverage"] == 0.0


def test_policy_keeps_staging_runner_outside_production_authority():
    """Runner 只产生评审证据，不能自行改生产默认值或授予生产 SLO 权威。"""

    policy = load_representative_staging_calibration_policy(ROOT)
    assert policy["version"] == 1
    assert policy["promotion"]["production_slo_authority"] is False
    assert policy["promotion"]["automatic_production_promotion"] is False
    assert policy["promotion"]["production_default_auto_update"] is False
    assert policy["promotion"]["explicit_human_approval_required"] is True
    assert policy["principles"][
        "timeout_admission_and_cross_tenant_probes_are_executed_not_self_attested"
    ] is True
