"""构建 Serving Consumer 的 DataHub 静态 Governance / Lineage Projection。

业务逻辑：把 Serving Policy、Consumer Registry 与 MetricFlow Serving Contract 合并成确定性的 Git 期望。
输出：``metadata/datahub/generated/serving_governance_projection.json``。
工程边界：本模块绝不写 DataHub，也不会把 expected URN 升级成 Runtime-verified identity。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
POLICY = ROOT / "metadata/datahub/governance/serving_policy.yml"
CONSUMERS = ROOT / "metadata/datahub/governance/consumer_registry.yml"
OUT = ROOT / "metadata/datahub/generated/serving_governance_projection.json"


def _yaml(path: Path) -> dict[str, Any]:
    """读取一个仓库内 YAML Contract；只作为静态声明，不代表 DataHub Live State。"""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _mart_expected_urn(model: str) -> str:
    """按项目统一 platform instance / env 规则生成 Mart 的 expected Iceberg Dataset URN。

    这是静态 Expected Identity；真实 Lineage 写入前仍必须由 Runtime ``graph.exists`` 精确验证。
    """
    name = f"commerce_polaris.analytics.{model}"
    return f"urn:li:dataset:(urn:li:dataPlatform:iceberg,{name},DEV)"


def build_projection() -> dict[str, Any]:
    """把 Serving Dataset、Dagster DataJob、BI Dashboard 与 API Endpoint 组合成静态治理投影。

    API Endpoint 的 ``expected_urn`` 故意保持 ``None``：OpenAPI ingestion 后才能得到真实 Dataset URN，禁止猜测。
    """
    policy = _yaml(POLICY)
    consumers = _yaml(CONSUMERS)
    items: list[dict[str, Any]] = []

    for asset in policy["serving_assets"]:
        dataset = asset["dataset"]
        contract = _yaml(ROOT / asset["contract_path"])
        upstream_models = asset["lineage"]["upstream_models"]
        flow_id = asset["lineage"]["export_flow_id"]
        job_id = asset["lineage"]["export_job_id"]
        serving_urn = dataset["expected_urn"]

        dashboard_edges = []
        for dashboard in consumers.get("bi", {}).get("dashboards", []):
            if dashboard["input_serving_asset"] == asset["id"]:
                dashboard_edges.append({
                    "entity_type": "dashboard",
                    "id": dashboard["id"],
                    "platform": dashboard["platform"],
                    "expected_urn": f"urn:li:dashboard:({dashboard['platform']},{dashboard['id']})",
                    "runtime_mode": dashboard["runtime_mode"],
                    "lineage": {"upstream": serving_urn},
                })

        api_edges = []
        for endpoint in consumers.get("api", {}).get("endpoints", []):
            if endpoint["input_serving_asset"] == asset["id"]:
                api_edges.append({
                    "entity_type": "dataset",
                    "id": endpoint["id"],
                    "method": endpoint["method"],
                    "path": endpoint["path"],
                    "identity_status": "RUNTIME_RESOLVE_AFTER_OPENAPI_INGESTION",
                    "expected_urn": None,
                    "resolved_urn": None,
                    "lineage": {"upstream": serving_urn},
                })

        items.append({
            "id": asset["id"],
            "dataset": {
                **dataset,
                "status": "UNVERIFIED_EXPECTED",
                "resolved_urn": None,
            },
            "governance": {
                "domain": asset["domain"],
                "owners": asset["owners"],
                "tags": asset["tags"],
                "glossary_terms": asset["glossary_terms"],
                "structured_properties": asset["structured_properties"],
            },
            "semantic_contract": {
                "metric_authority": policy["metric_authority"],
                "metrics": contract["semantic_query"]["metrics"],
                "group_by": contract["semantic_query"]["group_by"],
                "serving_contract": asset["contract_path"],
            },
            "export_job": {
                "entity_type": "datajob",
                "platform": "dagster",
                "platform_instance": "DEV",
                "flow_id": flow_id,
                "job_id": job_id,
                "upstream_datasets": [
                    {"model": model, "expected_urn": _mart_expected_urn(model)} for model in upstream_models
                ],
                "downstream_dataset": serving_urn,
            },
            "consumers": {
                "dashboards": dashboard_edges,
                "api_endpoints": api_edges,
            },
        })

    return {
        "contract": "commerce_serving_governance_projection",
        "mode": "STATIC_EXPECTATION_ONLY",
        "mutates_datahub": False,
        "runtime_verified": False,
        "principles": {
            "metric_authority": "MetricFlow",
            "serving_is_rebuildable_projection": True,
            "serving_agent_readiness": "REFERENCE_ONLY",
            "exact_identity_required_before_lineage_write": True,
            "api_endpoint_urn_guessing_forbidden": True,
        },
        "items": items,
    }


def main() -> None:
    """生成并落盘静态 Serving Governance Projection，供测试与未来 Runtime 工具读取。"""
    payload = build_projection()
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
