"""Representative Staging Evidence Manifest V1 的契约测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from acceptance.agent_slo.staging_evidence_manifest import (
    load_staging_evidence_policy,
    validate_staging_evidence_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def _manifest() -> dict:
    """构造满足 V1 全部门禁的最小代表性 Staging 清单。"""

    return {
        "schema_version": 1,
        "evidence_kind": "REPRESENTATIVE_STAGING_EVIDENCE_MANIFEST_V1",
        "environment": {
            "label": "representative-staging-shared-redis",
            "deployment_id": "staging-release-20260903-01",
            "captured_at": "2026-09-03T10:00:00+00:00",
            "git_sha": "a" * 40,
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


def _validate(manifest: dict) -> dict:
    """使用仓库版本化策略验证测试清单。"""

    return validate_staging_evidence_manifest(
        manifest,
        policy=load_staging_evidence_policy(ROOT),
    )


def test_complete_representative_staging_manifest_passes():
    """完整清单必须通过，并只输出有界的环境与数量摘要。"""

    report = _validate(_manifest())

    assert report["valid"] is True
    assert report["errors"] == []
    assert report["total_api_processes"] == 2
    assert report["total_requests"] == 100
    assert report["tenant_count"] == 2
    assert report["subject_count"] == 4
    assert report["production_slo_authority"] is False
    assert report["automatic_production_promotion"] is False


def test_topology_dependency_workload_tenancy_and_audit_are_all_required():
    """五类代表性证据任一缺失都必须失败，不能依赖环境 Label 放行。"""

    manifest = _manifest()
    manifest["topology"]["api_replicas"] = 1
    manifest["dependencies"]["metricflow"] = "stub"
    manifest["workload"]["intent_request_counts"].pop("ANALYSIS")
    manifest["tenancy"]["cross_tenant_isolation_probe_passed"] = False
    manifest["audit"]["runtime_audit_receipt_coverage"] = 0.99

    report = _validate(manifest)

    assert report["valid"] is False
    assert set(report["errors"]) >= {
        "API_REPLICA_COUNT_TOO_LOW",
        "TOTAL_API_PROCESS_COUNT_TOO_LOW",
        "LIVE_AUTHORITIES_INCOMPLETE",
        "WORKLOAD_REQUEST_COUNT_TOO_LOW",
        "REQUIRED_INTENT_MIX_INCOMPLETE",
        "CROSS_TENANT_ISOLATION_PROBE_REQUIRED",
        "RUNTIME_AUDIT_RECEIPT_COVERAGE_INCOMPLETE",
    }


def test_sensitive_fields_are_rejected_without_copying_values_to_report():
    """凭证、原始 Prompt 与 Trace ID 键不得进入清单或验证报告。"""

    manifest = _manifest()
    manifest["debug"] = {
        "raw_prompt": "sensitive question",
        "bearer_token": "secret-token",
    }
    report = _validate(manifest)

    assert report["valid"] is False
    assert "FORBIDDEN_SENSITIVE_FIELD_PRESENT" in report["errors"]
    serialized = str(report)
    assert "sensitive question" not in serialized
    assert "secret-token" not in serialized
    assert report["forbidden_sensitive_fields"] == [
        "bearer_token",
        "raw_prompt",
    ]


def test_manifest_git_sha_and_candidate_window_are_bounded():
    """清单必须使用完整 Git SHA，且审计窗口只能来自版本化候选矩阵。"""

    manifest = deepcopy(_manifest())
    manifest["environment"]["git_sha"] = "short-sha"
    manifest["audit"]["group_commit_window_ms"] = 9.0
    report = _validate(manifest)

    assert report["valid"] is False
    assert "GIT_SHA_INVALID" in report["errors"]
    assert "AUDIT_GROUP_COMMIT_WINDOW_NOT_GOVERNED" in report["errors"]


def test_staging_policy_v1_preserves_human_approval_boundary():
    """清单策略只负责验证环境，不能赋予生产 SLO 权威。"""

    policy = yaml.safe_load(
        (
            ROOT / "agent/contracts/agent_staging_evidence_policy.yml"
        ).read_text(encoding="utf-8")
    )
    assert policy["version"] == 1
    assert policy["topology"]["required_traffic_backend"] == "redis"
    assert policy["dependencies"][
        "trino_required_for_agent_dynamic_analysis"
    ] is False
    assert policy["promotion"]["production_slo_authority"] is False
    assert policy["promotion"]["automatic_production_promotion"] is False
    assert policy["promotion"]["explicit_human_approval_required"] is True
