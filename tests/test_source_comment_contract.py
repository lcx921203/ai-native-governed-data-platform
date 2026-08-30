from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Chapter 01–05 currently expose these Python modules directly in the engineering story.
# New core source files should be added here when they become part of the blog/source surface.
CORE_PYTHON_FILES = (
    "ingestion/shopify/extract_orders.py",
    "ingestion/shopify/source_config.py",
    "ingestion/shopify/load_fixtures.py",
    "ingestion/shopify/load_api_observations.py",
    "ingestion/mysql_cdc/flink/render_sql.py",
    "ingestion/behavior/collector/app.py",
    "ingestion/behavior/collector/producer.py",
    "ingestion/behavior/flink/job.py",
    "ingestion/behavior/flink/functions.py",
    "ingestion/behavior/flink/iceberg_sinks.py",
    "lakehouse/jobs/normalize_shopify_orders.py",
    "lakehouse/jobs/validate_business_version_rollback.py",
    "tests/validate_metricflow_golden_results.py",
    "infra/runtime/generate_metricflow_legacy.py",
    "orchestration/dagster/commerce_dagster/assets/lakehouse.py",
    "orchestration/dagster/commerce_dagster/partitions.py",
    "orchestration/dagster/commerce_dagster/automation_policy.py",
    "orchestration/dagster/commerce_dagster/freshness.py",
    "orchestration/dagster/commerce_dagster/recovery_state_current.py",
    "orchestration/dagster/commerce_dagster/recovery_policy.py",
    "orchestration/dagster/commerce_dagster/failure_classification.py",
    "orchestration/dagster/commerce_dagster/resources.py",
    "orchestration/dagster/commerce_dagster/dbt_failure_adapter.py",
    "orchestration/dagster/commerce_dagster/runtime_health.py",
    "orchestration/dagster/commerce_dagster/sensors.py",
    "metadata/datahub/tools/identity_resolver.py",
    "metadata/datahub/tools/build_governance_projection.py",
    "metadata/datahub/tools/apply_governance_projection.py",
    "metadata/datahub/tools/bootstrap_governance_entities.py",
    "metadata/datahub/tools/phase7_runtime.py",
    "agent/adapters/datahub_sdk.py",
    "agent/metadata_runtime.py",
    "agent/tools/governed_metadata.py",
)

CHINESE_FIRST_PYTHON_FILES = (
    "orchestration/dagster/commerce_dagster/consumer_sla.py",
    "orchestration/dagster/commerce_dagster/recovery_state_current.py",
    "orchestration/dagster/commerce_dagster/automation_policy.py",
    "orchestration/dagster/commerce_dagster/schedules.py",
    "orchestration/dagster/commerce_dagster/freshness.py",
    "orchestration/dagster/commerce_dagster/partitions.py",
    "orchestration/dagster/commerce_dagster/jobs.py",
    "orchestration/dagster/commerce_dagster/recovery_policy.py",
    "orchestration/dagster/commerce_dagster/failure_classification.py",
    "orchestration/dagster/commerce_dagster/resources.py",
    "orchestration/dagster/commerce_dagster/dbt_failure_adapter.py",
    "orchestration/dagster/commerce_dagster/runtime_health.py",
    "orchestration/dagster/commerce_dagster/sensors.py",
    "acceptance/phase3c/scenarios.py",
    "metadata/datahub/tools/phase7_runtime.py",
    "agent/adapters/datahub_sdk.py",
    "agent/metadata_runtime.py",
    "agent/tools/governed_metadata.py",
)


CHAPTER06_CHINESE_FIRST_PYTHON_FILES = (
    "agent/router/deterministic.py",
    "agent/router/executor.py",
    "agent/semantic_query/planner.py",
    "agent/semantic_query/executor.py",
    "agent/clarification/continuation.py",
    "agent/analysis_session/session.py",
    "agent/time_context/comparison.py",
    "agent/breakdown_analysis/analysis.py",
    "agent/anomaly_analysis/detector.py",
    "agent/driver_attribution/attribution.py",
    "agent/diagnostic/orchestrator.py",
    "agent/diagnostic/operational_health_current.py",
    "agent/incident_drilldown/drilldown.py",
    "agent/incident_response/planner.py",
    "agent/approval_workflow/workflow.py",
    "agent/response/composer.py",
    "agent/response/validator.py",
)


CHAPTER07_CHINESE_FIRST_PYTHON_FILES = (
    "agent/knowledge/models.py",
    "agent/knowledge/document_ingestion.py",
    "agent/knowledge/corpus.py",
    "agent/knowledge/chunking.py",
    "agent/knowledge/embeddings.py",
    "agent/knowledge/qdrant_store.py",
    "agent/knowledge/reranker.py",
    "agent/knowledge/retrieval.py",
    "agent/knowledge/evaluation.py",
    "agent/knowledge/indexer.py",
    "agent/knowledge/tools.py",
    "agent/knowledge/hybrid.py",
)


CHAPTER08_CHINESE_FIRST_PYTHON_FILES = (
    "mcp_server/models.py",
    "mcp_server/registry.py",
    "mcp_server/server.py",
    "mcp_server/resources.py",
    "mcp_server/prompts.py",
    "mcp_server/auth/jwt.py",
    "mcp_server/auth/scopes.py",
    "mcp_server/auth/profiles.py",
    "infra/runtime/phase7/mcp_runtime_acceptance.py",
    "infra/runtime/phase7/collect_phase7_final_evidence.py",
)


CHAPTER09_CHINESE_FIRST_PYTHON_FILES = (
    "serving/contracts.py",
    "serving/exporter.py",
    "serving/export_cli.py",
    "serving/jobs/materialize_export.py",
    "serving/api/main.py",
    "serving/api/models.py",
    "serving/api/queries.py",
    "serving/api/repository.py",
    "serving/api/settings.py",
    "serving/api/export_openapi.py",
    "orchestration/dagster/commerce_dagster/assets/serving.py",
    "orchestration/dagster/commerce_dagster/serving_readiness.py",
    "metadata/datahub/tools/build_serving_governance_projection.py",
    "metadata/datahub/tools/serving_runtime.py",
    "metadata/datahub/tools/resolve_serving_consumer_identities.py",
    "infra/runtime/serving_runtime_acceptance.py",
)


STRUCTURED_COMMENT_CONTRACTS = {
    "ingestion/mysql_cdc/flink/item_store_cdc.sql.tmpl": ("Checkpoint", "Sink Contract", "工程边界"),
    "ingestion/shopify/queries/orders.graphql": ("业务逻辑", "GraphQL API", "工程边界"),
    "ingestion/shopify/queries/order_line_items_page.graphql": ("GraphQL API", "工程边界"),
    "dbt/mercaso_dbt/models/sources/shopify.yml": ("业务逻辑", "dbt API", "工程边界"),
    "dbt/mercaso_dbt/models/staging/shopify/stg_shopify__orders.sql": ("输入", "输出", "工程边界"),
    "dbt/mercaso_dbt/models/intermediate/shopify/int_shopify__orders_canonical.sql": ("changed_keys", "candidate_pool", "工程边界"),
    "dbt/mercaso_dbt/models/marts/commerce/order_lifecycle_snapshot.sql": ("affected_order_ids", "payment_milestones", "工程边界"),
    "dbt/mercaso_dbt/models/marts/commerce/order_items.sql": ("Affected-Key", "affected_line_item_ids", "Grain"),
    "dbt/mercaso_dbt/models/marts/commerce/refund_items.sql": ("Affected-Key", "affected_refund_line_item_ids", "Grain"),
    "dbt/mercaso_dbt/models/metrics/sales.yml": ("MetricFlow API", "工程边界"),
    "dbt/mercaso_dbt/models/metrics/lifecycle.yml": ("MetricFlow API", "Order Grain", "工程边界"),
    "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml": ("MetricFlow API", "Join Safety", "工程边界"),
    "tests/expected/commerce_metrics.yml": ("Golden Oracle", "工程边界"),
    "infra/runtime/run_metricflow_validation.sh": ("API/CLI", "negative", "工程边界"),
    "metadata/datahub/governance/asset_policy.yml": ("业务逻辑", "DataHub API", "工程边界", "REFERENCE_ONLY"),
    "metadata/datahub/governance/glossary.yml": ("业务逻辑", "DataHub API", "工程边界"),
    "metadata/datahub/governance/owners.yml": ("业务逻辑", "DataHub API", "工程边界"),
    "metadata/datahub/contracts/agent_read_contract.yml": ("业务逻辑", "DataHub API", "工程边界", "STATIC_CONTRACT"),
    "agent/contracts/metadata_runtime_cutover_policy.yml": ("业务逻辑", "DataHub API", "工程边界", "fail closed"),
    "infra/contracts/phase7/datahub_runtime_verification.yml": ("业务逻辑", "DataHub API", "工程边界", "Runtime Evidence"),
    "agent/contracts/phase5_capability_manifest.yml": ("业务逻辑", "Runtime gate", "工程边界"),
    "agent/contracts/phase6_capability_manifest.yml": ("业务逻辑", "APPROVED", "工程边界"),
    "agent/contracts/claim_authority.yml": ("业务逻辑", "primary evidence", "工程边界"),
    "agent/contracts/approval_workflow_policy.yml": ("业务逻辑", "HUMAN_REQUIRED", "工程边界"),
    "agent/contracts/knowledge_policy.yml": ("业务逻辑", "Authority Contract", "Evidence Contract"),
    "agent/contracts/knowledge_retrieval_policy.yml": ("业务逻辑", "Qdrant Dense Retrieval", "Runtime Gate"),
    "metadata/knowledge/corpus_manifest.yml": ("Source Authority", "Front Matter", "PDF / DOCX", "SOURCE DEFINED"),
    "agent/contracts/intent_routing.yml": ("Knowledge Intent", "结构化权威", "structured_authority_precedes_rag"),
    "metadata/knowledge/retrieval_eval_cases.yml": ("Golden Retrieval Evaluation", "Recall@5"),
    "infra/contracts/phase7/commerce_mcp.yml": ("mcp_is_protocol_adapter", "read_only_surface", "arbitrary_sql"),
    "infra/contracts/phase7/mcp_security.yml": ("oauth_required", "token_passthrough", "governed_registry_controls_execution"),
    "infra/contracts/phase7/mcp_runtime_acceptance.yml": ("structured_tool_output", "unauthorized_request_rejected", "COMMERCE_MCP_RUNTIME_VERIFIED"),
    "infra/contracts/phase7/phase7_final_closure.yml": ("required_evidence", "authority_audit", "PHASE7_END_TO_END_RUNTIME_VERIFIED"),
    "serving/contracts/bi_daily_executive.yml": ("MetricFlow", "Grain", "工程边界"),
    "metadata/datahub/governance/serving_policy.yml": ("Metric Authority", "REFERENCE_ONLY", "工程边界"),
    "metadata/datahub/governance/consumer_registry.yml": ("Dashboard", "OpenAPI", "工程边界"),
    "infra/trino/catalog/iceberg.properties": ("Metric definitions", "Polaris REST Catalog", "RustFS"),
    "metadata/datahub/recipes/serving_api_openapi.yml": ("OpenAPI", "exact URN", "工程边界"),
}


def test_core_python_functions_and_methods_have_local_docstrings():
    missing: list[str] = []
    for rel in CORE_PYTHON_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not ast.get_docstring(node):
                missing.append(f"{rel}:{node.lineno}:{node.name}")
    assert not missing, "Core function/method docstrings missing: " + ", ".join(missing)


def test_chapter06_python_explanations_are_chinese_first():
    """受治理分析 / 诊断主源码的 module、class、function 说明必须中文优先。"""
    missing: list[str] = []
    for rel in CHAPTER06_CHINESE_FIRST_PYTHON_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _contains_chinese(ast.get_docstring(tree)):
            missing.append(f"{rel}:module")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # __init__ 属于 Python 构造器，当前规范允许由 class docstring 承担说明。
                if getattr(node, "name", "") == "__init__":
                    continue
                if not _contains_chinese(ast.get_docstring(node)):
                    missing.append(f"{rel}:{node.lineno}:{node.name}")
    assert not missing, "Chapter 06 Chinese-first docstring contract failed: " + ", ".join(missing)



def test_chapter07_python_explanations_are_chinese_first():
    """Knowledge RAG 核心源码的 module、class、function/method 说明必须中文优先。"""
    missing: list[str] = []
    for rel in CHAPTER07_CHINESE_FIRST_PYTHON_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _contains_chinese(ast.get_docstring(tree)):
            missing.append(f"{rel}:module")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not _contains_chinese(ast.get_docstring(node)):
                    missing.append(f"{rel}:{node.lineno}:{node.name}")
    assert not missing, "Chapter 07 Chinese-first docstring contract failed: " + ", ".join(missing)


def test_chapter08_python_explanations_are_chinese_first():
    """MCP / 最终运行闭环主源码的 module、class、function/method 说明必须中文优先。"""
    missing: list[str] = []
    for rel in CHAPTER08_CHINESE_FIRST_PYTHON_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _contains_chinese(ast.get_docstring(tree)):
            missing.append(f"{rel}:module")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if getattr(node, "name", "") == "__init__":
                    continue
                if not _contains_chinese(ast.get_docstring(node)):
                    missing.append(f"{rel}:{node.lineno}:{node.name}")
    assert not missing, "Chapter 08 Chinese-first docstring contract failed: " + ", ".join(missing)

def test_chapter09_python_explanations_are_chinese_first():
    """Serving / Trino / BI / API / Consumer Governance 主源码必须保持中文优先解释。"""
    missing: list[str] = []
    for rel in CHAPTER09_CHINESE_FIRST_PYTHON_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _contains_chinese(ast.get_docstring(tree)):
            missing.append(f"{rel}:module")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if getattr(node, "name", "") == "__init__":
                    continue
                if not _contains_chinese(ast.get_docstring(node)):
                    missing.append(f"{rel}:{node.lineno}:{node.name}")
    assert not missing, "Chapter 09 Chinese-first docstring contract failed: " + ", ".join(missing)


def test_structured_source_files_keep_local_explanation_markers():
    missing: list[str] = []
    for rel, markers in STRUCTURED_COMMENT_CONTRACTS.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                missing.append(f"{rel}: {marker}")
    assert not missing, "Structured comment contract markers missing: " + ", ".join(missing)


def test_comment_standard_document_exists_and_preserves_evidence_boundary():
    text = (ROOT / "docs/SOURCE_COMMENT_STANDARD.md").read_text(encoding="utf-8")
    for marker in (
        "Business / code logic",
        "Framework / API knowledge",
        "Engineering boundary",
        "SOURCE / STATIC PASS is not Runtime PASS",
        "APPROVED is not EXECUTED",
    ):
        assert marker in text


def _contains_chinese(text: str | None) -> bool:
    if not text:
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def test_chapter04_05_python_explanations_are_chinese_first():
    """编排 / DataHub 主源码的 module、class、function 说明必须包含中文解释。"""
    missing: list[str] = []
    for rel in CHINESE_FIRST_PYTHON_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _contains_chinese(ast.get_docstring(tree)):
            missing.append(f"{rel}:module")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not _contains_chinese(ast.get_docstring(node)):
                    missing.append(f"{rel}:{node.lineno}:{node.name}")
    assert not missing, "Chinese-first docstring contract failed: " + ", ".join(missing)
