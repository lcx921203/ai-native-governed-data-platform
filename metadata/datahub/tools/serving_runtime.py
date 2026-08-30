"""Serving Dataset、BI、API Consumer 的 Runtime-gated DataHub 集成。

静态 Contract 只表达治理意图；只有 exact DataHub Identity 已经存在并被重新查询验证后，才允许写 Governance / Lineage。
API Endpoint Dataset URN 绝不猜测，必须由 OpenAPI ingestion 后的精确身份 Evidence 提供。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
PROJECTION = ROOT / "metadata/datahub/generated/serving_governance_projection.json"
API_IDENTITIES = ROOT / ".runtime/evidence/serving/datahub/api_endpoint_identities.json"
EVIDENCE_DIR = ROOT / ".runtime/evidence/serving/datahub"


class ServingGovernanceRuntimeError(RuntimeError):
    """Serving Governance 的 Gate、Identity、Mutation 或 final re-query 失败时抛出的受控异常。"""

    pass


def _require_gate(name: str) -> None:
    """要求指定 DataHub 写 Gate 显式为 true；所有 mutation 默认 Fail Closed。"""
    if os.getenv(name, "false").lower() != "true":
        raise ServingGovernanceRuntimeError(f"REFUSED: set {name}=true explicitly")


def _client():
    """构造 mutation-capable DataHub SDK Client；仅由已通过 Gate 的上层写函数使用。"""
    try:
        from datahub.sdk import DataHubClient
    except ImportError as exc:
        raise ServingGovernanceRuntimeError("acryl-datahub SDK is required for real Serving governance runtime") from exc
    return DataHubClient.from_env()


def _graph():
    """构造 DataHubGraph 精确读取客户端，用于 exists / raw aspect re-query。"""
    try:
        from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
    except ImportError as exc:
        raise ServingGovernanceRuntimeError("acryl-datahub graph client is required for exact identity verification") from exc
    server = os.getenv("DATAHUB_GMS_URL", "http://localhost:8080")
    token = os.getenv("DATAHUB_GMS_TOKEN")
    return DataHubGraph(DatahubClientConfig(server=server, token=token))


def _load_projection() -> dict[str, Any]:
    """读取 Git 生成的 Serving Governance Projection；文件缺失时拒绝 Runtime 写入。"""
    if not PROJECTION.exists():
        raise ServingGovernanceRuntimeError("Build serving_governance_projection.json first")
    return json.loads(PROJECTION.read_text(encoding="utf-8"))


def _collect_strings(value: Any) -> list[str]:
    """把 DataHub Raw Aspect 递归展开为文本，供 final re-query 做确定性包含校验。"""
    result: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            result.extend(_collect_strings(k))
            result.extend(_collect_strings(v))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(_collect_strings(item))
    elif value is not None:
        result.append(str(value))
    return result


def resolve_serving_dataset(*, graph: Any | None = None) -> dict[str, Any]:
    """解析并验证 Iceberg Serving Dataset 的 exact URN；禁止 search / fuzzy binding。

    还会验证 Data Platform Instance，避免“同名 Dataset、错误实例”被误绑定。
    """
    graph = graph or _graph()
    item = _load_projection()["items"][0]
    dataset = item["dataset"]
    urn = dataset["expected_urn"]
    if not graph.exists(urn):
        raise ServingGovernanceRuntimeError(f"Serving Dataset exact URN not found: {urn}")
    raw = graph.get_entity_raw(urn, aspects=["dataPlatformInstance"])
    strings = _collect_strings(raw)
    if dataset["platform_instance"] not in "\n".join(strings):
        raise ServingGovernanceRuntimeError("Serving Dataset platform instance does not match commerce_polaris")
    return {**dataset, "status": "RESOLVED_EXPECTED", "resolved_urn": urn, "runtime_verified": True}


def apply_serving_dataset_governance(*, client: Any | None = None, graph: Any | None = None) -> dict[str, Any]:
    """向 exact Serving Dataset 写 Domain / Owner / Tag / Term / Structured Property，并 final re-query。

    只有所有预期 Aspect 都能从 DataHub 实时读回，才返回 VERIFIED；Expected Projection 本身不算成功证据。
    """
    _require_gate("SERVING_GOVERNANCE_ALLOW_DATAHUB_WRITE")
    client = client or _client()
    graph = graph or _graph()
    item = _load_projection()["items"][0]
    identity = resolve_serving_dataset(graph=graph)
    spec = item["governance"]

    try:
        from datahub.emitter.mce_builder import make_group_urn, make_tag_urn, make_term_urn
        from datahub.metadata.schema_classes import GlossaryTermAssociationClass, OwnerClass, OwnershipTypeClass, TagAssociationClass
        from datahub.metadata.urns import DomainUrn
        from datahub.specific.dataset import DatasetPatchBuilder
    except ImportError as exc:
        raise ServingGovernanceRuntimeError("DataHub Dataset governance patch APIs are unavailable") from exc

    dataset_entity = client.entities.get(identity["resolved_urn"])
    dataset_entity.set_domain(DomainUrn(id=spec["domain"]))
    client.entities.update(dataset_entity)

    patch = DatasetPatchBuilder(identity["resolved_urn"])
    patch.add_owner(OwnerClass(owner=make_group_urn(spec["owners"]["business"]), type=OwnershipTypeClass.BUSINESS_OWNER))
    patch.add_owner(OwnerClass(owner=make_group_urn(spec["owners"]["technical"]), type=OwnershipTypeClass.TECHNICAL_OWNER))
    for tag in spec["tags"]:
        patch.add_tag(TagAssociationClass(tag=make_tag_urn(tag)))
    for term in spec["glossary_terms"]:
        patch.add_term(GlossaryTermAssociationClass(urn=make_term_urn(term)))
    for key, value in spec["structured_properties"].items():
        patch.add_structured_property(f"urn:li:structuredProperty:{key}", value)
    client.entities.update(patch)

    raw = graph.get_entity_raw(
        identity["resolved_urn"],
        aspects=["domains", "ownership", "globalTags", "glossaryTerms", "structuredProperties"],
    )
    text = "\n".join(_collect_strings(raw))
    checks = {
        "domain": f"urn:li:domain:{spec['domain']}" in text,
        "business_owner": f"urn:li:corpGroup:{spec['owners']['business']}" in text,
        "technical_owner": f"urn:li:corpGroup:{spec['owners']['technical']}" in text,
        "tags": all(f"urn:li:tag:{tag}" in text for tag in spec["tags"]),
        "glossary_terms": all(f"urn:li:glossaryTerm:{term}" in text for term in spec["glossary_terms"]),
        "structured_properties": all(
            f"urn:li:structuredProperty:{key}" in text and str(value) in text
            for key, value in spec["structured_properties"].items()
        ),
    }
    if not all(checks.values()):
        raise ServingGovernanceRuntimeError(f"Serving governance final re-query failed: {checks}")
    return {"status": "SERVING_DATASET_GOVERNANCE_VERIFIED", "dataset_urn": identity["resolved_urn"], "checks": checks}


def upsert_export_job_and_lineage(*, client: Any | None = None, graph: Any | None = None) -> dict[str, Any]:
    """把 Dagster Serving Export 表达为 DataFlow/DataJob，并绑定 exact Dataset Inlets / Outlet。

    所有上游 Mart 与 Serving Dataset 必须先按 exact URN 存在；任何一个缺失都拒绝写 Lineage。
    """
    _require_gate("SERVING_GOVERNANCE_ALLOW_LINEAGE_WRITE")
    client = client or _client()
    graph = graph or _graph()
    item = _load_projection()["items"][0]
    serving_identity = resolve_serving_dataset(graph=graph)
    job = item["export_job"]

    # Every Mart inlet must already exist at its exact repository-declared URN.
    for upstream in job["upstream_datasets"]:
        if not graph.exists(upstream["expected_urn"]):
            raise ServingGovernanceRuntimeError(f"Upstream exact Dataset URN not found: {upstream['expected_urn']}")

    try:
        from datahub.metadata.urns import DatasetUrn, TagUrn
        from datahub.sdk import DataFlow, DataJob
    except ImportError as exc:
        raise ServingGovernanceRuntimeError("DataHub DataFlow/DataJob SDK is unavailable") from exc

    flow = DataFlow(
        name=job["flow_id"],
        platform="dagster",
        platform_instance=job["platform_instance"],
        description="Dagster flow that materializes governed MetricFlow queries into Iceberg Serving projections.",
        tags=[TagUrn(name="layer-serving"), TagUrn(name="metricflow-governed")],
    )
    inlets = [
        DatasetUrn(platform="iceberg", name=x["expected_urn"].split(",", 1)[1].rsplit(",", 1)[0], env="DEV")
        for x in job["upstream_datasets"]
    ]
    outlet = DatasetUrn(platform="iceberg", name=item["dataset"]["dataset_name"], env="DEV")
    datajob = DataJob(name=job["job_id"], flow=flow, inlets=inlets, outlets=[outlet])
    client.entities.upsert(flow)
    client.entities.upsert(datajob)
    if not graph.exists(str(flow.urn)) or not graph.exists(str(datajob.urn)):
        raise ServingGovernanceRuntimeError("Serving DataFlow/DataJob final re-query failed")

    return {
        "status": "SERVING_EXPORT_JOB_UPSERTED",
        "flow": str(flow.urn),
        "job": str(datajob.urn),
        "inlets": [x["expected_urn"] for x in job["upstream_datasets"]],
        "outlet": serving_identity["resolved_urn"],
    }


def upsert_logical_dashboard(*, client: Any | None = None, graph: Any | None = None) -> dict[str, Any]:
    """创建仓库自有的逻辑 Dashboard Contract 与 Dataset → Dashboard Lineage。

    这是未接真实 BI 平台时的治理占位实体；接入 Tableau/Power BI 等后应替换为原生 Dashboard Identity。
    """
    _require_gate("SERVING_GOVERNANCE_ALLOW_CONSUMER_WRITE")
    client = client or _client()
    graph = graph or _graph()
    item = _load_projection()["items"][0]
    dashboard = item["consumers"]["dashboards"][0]
    try:
        from datahub.metadata.urns import TagUrn
        from datahub.sdk import Dashboard
    except ImportError as exc:
        raise ServingGovernanceRuntimeError("DataHub Dashboard SDK is unavailable") from exc

    entity = Dashboard(
        name=dashboard["id"],
        platform=dashboard["platform"],
        description="Logical executive dashboard contract. Replace with the native BI dashboard identity when a real BI platform is connected.",
        tags=[TagUrn(name="consumer-bi"), TagUrn(name="metricflow-governed")],
        input_datasets=[dashboard["lineage"]["upstream"]],
    )
    client.entities.upsert(entity)
    if not graph.exists(str(entity.urn)):
        raise ServingGovernanceRuntimeError("Logical Dashboard final re-query failed")
    return {"status": "LOGICAL_DASHBOARD_UPSERTED", "dashboard_urn": str(entity.urn), "runtime_verified": True}


def apply_api_endpoint_lineage(*, client: Any | None = None, graph: Any | None = None) -> dict[str, Any]:
    """使用精确解析的 Endpoint URN 写 Serving Dataset → OpenAPI Endpoint Dataset Lineage。

    Identity Artifact 必须来自 OpenAPI ingestion 之后的精确验证；本函数既不按名称搜索，也不制造 Endpoint URN。
    """
    _require_gate("SERVING_GOVERNANCE_ALLOW_LINEAGE_WRITE")
    client = client or _client()
    graph = graph or _graph()
    serving = resolve_serving_dataset(graph=graph)["resolved_urn"]
    if not API_IDENTITIES.exists():
        raise ServingGovernanceRuntimeError(
            "REFUSED: exact API endpoint identity evidence is missing; ingest OpenAPI and resolve endpoint Dataset URNs first"
        )
    payload = json.loads(API_IDENTITIES.read_text(encoding="utf-8"))
    endpoints = payload.get("endpoints") or []
    if not endpoints or not all(x.get("resolved_urn") and x.get("runtime_verified") for x in endpoints):
        raise ServingGovernanceRuntimeError("REFUSED: API endpoint identities are not exact runtime-verified URNs")
    for endpoint in endpoints:
        urn = endpoint["resolved_urn"]
        if not graph.exists(urn):
            raise ServingGovernanceRuntimeError(f"API endpoint Dataset no longer exists: {urn}")
        client.lineage.add_lineage(upstream=serving, downstream=urn)
        raw = graph.get_entity_raw(urn, aspects=["upstreamLineage"])
        if serving not in "\n".join(_collect_strings(raw)):
            raise ServingGovernanceRuntimeError(f"API endpoint lineage final re-query failed: {urn}")
    return {"status": "API_ENDPOINT_LINEAGE_APPLIED", "upstream": serving, "downstreams": [x["resolved_urn"] for x in endpoints], "runtime_verified": True}


def verify_full_serving_governance_runtime() -> dict[str, Any]:
    """执行 Serving Governance 全链路并生成可供最终闭环使用的 Runtime Payload。

    前置条件：OpenAPI 已 ingestion、Endpoint exact URN evidence 已生成，三个 mutation gate 均显式开启。
    验证范围：Serving Dataset Governance、Dagster DataFlow/DataJob、Logical Dashboard、API Endpoint Lineage。
    """
    client = _client()
    graph = _graph()
    identity = resolve_serving_dataset(graph=graph)
    governance = apply_serving_dataset_governance(client=client, graph=graph)
    export_job = upsert_export_job_and_lineage(client=client, graph=graph)
    dashboard = upsert_logical_dashboard(client=client, graph=graph)
    api_lineage = apply_api_endpoint_lineage(client=client, graph=graph)
    payload = {
        "contract": "commerce_serving_governance_runtime",
        "runtime_verified": True,
        "status": "SERVING_GOVERNANCE_RUNTIME_VERIFIED",
        "components": {
            "serving_dataset_identity": identity,
            "serving_dataset_governance": governance,
            "serving_export_datajob": export_job,
            "logical_dashboard": dashboard,
            "api_endpoint_lineage": api_lineage,
        },
    }
    write_runtime_evidence(payload)
    return payload


def write_runtime_evidence(payload: dict[str, Any]) -> Path:
    """把已真实执行的 Serving Governance 结果写入 ``.runtime`` Evidence；不会修改 Git/static truth。"""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / "serving_governance_runtime.json"
    body = {"collected_at": datetime.now(timezone.utc).isoformat(), **payload}
    out.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def main() -> int:
    """DataHub Serving Governance CLI；每个命令仍受对应环境 Gate 与 exact Identity 检查约束。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=[
        "resolve-serving-dataset",
        "apply-serving-governance",
        "upsert-export-job",
        "upsert-logical-dashboard",
        "apply-api-lineage",
        "verify-all",
    ])
    args = parser.parse_args()
    try:
        if args.command == "resolve-serving-dataset":
            payload = resolve_serving_dataset()
        elif args.command == "apply-serving-governance":
            payload = apply_serving_dataset_governance()
        elif args.command == "upsert-export-job":
            payload = upsert_export_job_and_lineage()
        elif args.command == "upsert-logical-dashboard":
            payload = upsert_logical_dashboard()
        elif args.command == "apply-api-lineage":
            payload = apply_api_endpoint_lineage()
        else:
            payload = verify_full_serving_governance_runtime()
    except ServingGovernanceRuntimeError as exc:
        print(str(exc))
        return 2
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
