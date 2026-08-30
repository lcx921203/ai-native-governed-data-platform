"""Phase 7A 的 DataHub Live Bootstrap / Exact Identity / Governance Acceptance 实现。

这层负责未来真实 DataHub workstation runtime 的治理闭环，但每一次 mutation 都必须经过显式 Gate。
历史 Phase 4/6 包继续作为当时的静态证据；当前 canonical source 可以正常演进。

Authority Boundary（权威边界）：
- Dataset Identity 只能按 exact URN 绑定，禁止 fuzzy search；
- 只有 exact expected Dataset URN 已存在，才允许投影 Governance；
- 只有 final re-query 真正读回预期治理结果后，才允许生成 Runtime Evidence；
- Runtime Artifact 只写入 ``.runtime/``，不能反向修改 Git/static truth。
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = ROOT / ".runtime/evidence/phase7a/datahub"
STATIC_IDENTITIES = ROOT / "metadata/datahub/generated/dataset_identity_resolution.json"


class Phase7DataHubRuntimeError(RuntimeError):
    """Phase 7 DataHub Runtime 合同无法满足时抛出的显式异常。

    使用专用异常类型让 Acceptance / CLI 能区分“治理闭环失败”与普通 Python 错误。
    """

    pass


def _yaml(rel: str) -> dict[str, Any]:
    """读取一个项目内 YAML 合同并返回 Python 字典。
    
    输入：相对项目根目录的 YAML 路径。
    输出：safe_load 后的结构化字典。
    工程边界：这里只读取 Git/static contract，不把其内容自动视为 DataHub Runtime truth。"""
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _require_gate(name: str) -> None:
    """要求指定 Runtime 环境门显式为 true。
    
    输入：环境变量名称。
    行为：缺失或非 true 时抛出 Phase7DataHubRuntimeError。
    工程边界：任何 DataHub bootstrap / governance mutation 都必须显式 opt-in，默认 fail closed。"""
    if os.getenv(name, "false").lower() != "true":
        raise Phase7DataHubRuntimeError(f"REFUSED: set {name}=true explicitly")


def _graph():
    """构造 DataHubGraph 低层读取客户端。
    
    DataHub API：DataHubGraph 用于 exists()、get_entity_raw() 等精确实体读取。
    输入：DATAHUB_GMS_URL / DATAHUB_GMS_TOKEN 环境变量。
    输出：DataHubGraph。
    工程边界：SDK 未安装时直接失败，不降级成伪 Runtime。"""
    try:
        from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
    except ImportError as exc:
        raise Phase7DataHubRuntimeError(
            "DataHub Python SDK is unavailable; install requirements-datahub.txt in .venv-datahub"
        ) from exc
    return DataHubGraph(
        DatahubClientConfig(
            server=os.getenv("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.getenv("DATAHUB_GMS_TOKEN") or None,
        )
    )


def _rest_emitter():
    """构造 DataHub REST emitter，用于发送治理实体 MCP。
    
    DataHub API：DatahubRestEmitter + MetadataChangeProposal 用于 Domain / CorpGroup 等治理定义写入。
    输出：DatahubRestEmitter。
    工程边界：只有显式 bootstrap gate 打开后，上层函数才允许调用。"""
    try:
        from datahub.emitter.rest_emitter import DatahubRestEmitter
    except ImportError as exc:
        raise Phase7DataHubRuntimeError("DataHub REST emitter is unavailable") from exc
    return DatahubRestEmitter(
        gms_server=os.getenv("DATAHUB_GMS_URL", "http://localhost:8080"),
        token=os.getenv("DATAHUB_GMS_TOKEN") or None,
    )


def _sdk_client():
    """构造 DataHub 高层 SDK Client。
    
    DataHub API：DataHubClient 用于 Dataset、Tag、GlossaryTerm 等实体的受控读写。
    输出：从环境配置创建的客户端。
    工程边界：Agent 读取面不直接暴露此 mutation-capable client。"""
    try:
        from datahub.sdk import DataHubClient
    except ImportError as exc:
        raise Phase7DataHubRuntimeError("DataHub high-level SDK is unavailable") from exc
    return DataHubClient.from_env()


def _static_identities() -> list[dict[str, Any]]:
    """读取 Git 中的 expected Dataset identity 静态合同。
    
    输入：metadata/datahub/generated/dataset_identity_resolution.json。
    输出：非空 identity 列表。
    工程边界：这些 identity 是 expected contract；后续必须由 real DataHub exists/re-query 才能变成 Runtime verified。"""
    payload = json.loads(STATIC_IDENTITIES.read_text(encoding="utf-8"))
    identities = payload.get("identities") or []
    if not identities:
        raise Phase7DataHubRuntimeError("Static Dataset identity contract is empty")
    return identities


def _contains(value: Any, needle: str) -> bool:
    """递归判断 DataHub 原始 aspect 结构中是否包含指定字符串。
    
    输入：dict/list/scalar 任意嵌套值与 needle。
    输出：bool。
    用途：对 SDK 返回形状保持一定兼容性，用于 platform instance 等精确治理检查。"""
    if isinstance(value, dict):
        return any(_contains(k, needle) or _contains(v, needle) for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(_contains(item, needle) for item in value)
    return needle in str(value)


def _collect_strings(value: Any) -> list[str]:
    """递归收集 DataHub 原始 aspect 中可比较的字符串。
    
    输入：DataHub raw aspect 的嵌套 dict/list/set。
    输出：扁平字符串列表。
    用途：最终 re-query 时检查 Domain / Owner / Tag / Term / Structured Property / lineage URN。"""
    result: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            result.extend(_collect_strings(k)); result.extend(_collect_strings(v))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(_collect_strings(item))
    elif value is not None:
        result.append(str(value))
    return result


def bootstrap_governance_definitions() -> dict[str, Any]:
    """创建或更新仓库声明的 Domain、Group、Tag、Glossary Term 与 Structured Property。

    DataHub API：这里先创建 Governance Definition，因为后续 Dataset Aspect 会引用这些实体。
    工程边界：Bootstrap Gate 只允许创建治理定义；真正修改 Consumer Dataset Governance
    还必须通过更强的 Governance Write Gate。定义创建成功 ≠ Dataset 已治理完成。
    """
    _require_gate("PHASE7A_ALLOW_DATAHUB_BOOTSTRAP")
    domains = _yaml("metadata/datahub/governance/domains.yml")
    owners = _yaml("metadata/datahub/governance/owners.yml")
    tags = _yaml("metadata/datahub/governance/tags.yml")
    glossary = _yaml("metadata/datahub/governance/glossary.yml")
    properties = _yaml("metadata/datahub/governance/structured_properties.yml")

    try:
        from datahub.api.entities.structuredproperties.structuredproperties import AllowedValue, StructuredProperties
        from datahub.emitter.mce_builder import make_domain_urn, make_group_urn
        from datahub.emitter.mcp import MetadataChangeProposalWrapper
        from datahub.metadata.schema_classes import CorpGroupInfoClass, DomainPropertiesClass
        from datahub.sdk import DataHubClient, Tag
        from datahub.sdk.glossary_term import GlossaryTerm
    except ImportError as exc:
        raise Phase7DataHubRuntimeError("Installed DataHub SDK does not expose the required governance APIs") from exc

    emitter = _rest_emitter()
    client = _sdk_client()
    emitted = {"domains": 0, "groups": 0, "tags": 0, "glossary_terms": 0, "structured_properties": 0}

    domain_items = [domains["root"], *domains.get("subdomains", [])]
    for item in domain_items:
        aspect = DomainPropertiesClass(
            name=item["name"],
            description=item.get("description") or "",
            parentDomain=make_domain_urn(item["parent"]) if item.get("parent") else None,
        )
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=make_domain_urn(item["id"]), aspect=aspect))
        emitted["domains"] += 1

    for item in owners.get("groups", []):
        aspect = CorpGroupInfoClass(displayName=item["display_name"], description=item.get("purpose") or "")
        emitter.emit_mcp(MetadataChangeProposalWrapper(entityUrn=make_group_urn(item["id"]), aspect=aspect))
        emitted["groups"] += 1

    for item in tags.get("tags", []):
        client.entities.upsert(Tag(name=item["id"], display_name=item["display_name"], description=item.get("description") or ""))
        emitted["tags"] += 1

    for item in glossary.get("terms", []):
        client.entities.upsert(
            GlossaryTerm(id=item["id"], display_name=item["name"], definition=item.get("description") or item["name"])
        )
        emitted["glossary_terms"] += 1

    for item in properties.get("properties", []):
        entity_types = [urn.rsplit(".", 1)[-1] for urn in item.get("entity_types", [])]
        prop = StructuredProperties(
            id=item["id"],
            qualified_name=item["qualified_name"],
            display_name=item["display_name"],
            type=item["type"],
            description=item.get("description") or "",
            entity_types=entity_types,
            cardinality=item.get("cardinality", "SINGLE"),
            allowed_values=[AllowedValue(value=x["value"], description=x.get("description") or "") for x in item.get("allowed_values", [])],
        )
        for mcp in prop.generate_mcps():
            emitter.emit_mcp(mcp)
        emitted["structured_properties"] += 1

    return {"status": "GOVERNANCE_DEFINITIONS_BOOTSTRAPPED", "counts": emitted}


def resolve_exact_identities(*, graph: Any | None = None, write: bool = True) -> dict[str, Any]:
    """只解析仓库明确声明的 exact Dataset URN。

    DataHub API：这里完全不调用 Search API；Dataset 要么存在于 expected URN，要么 Fail Closed。
    expected platform / env 已编码进 URN，同时还必须从实体读取并验证 platform instance。
    工程边界：禁止 fuzzy name search 把“相似 Dataset”绑定成当前业务资产。
    """
    graph = graph or _graph()
    rows = []
    all_verified = True
    for item in _static_identities():
        urn = item["expected_urn"]
        exists = bool(graph.exists(urn))
        raw = graph.get_entity_raw(urn, aspects=["dataPlatformInstance"]) if exists else {}
        instance_ok = exists and _contains(raw, item["platform_instance"])
        status = "RESOLVED_EXPECTED" if exists and instance_ok else (
            "BLOCKED_PLATFORM_INSTANCE_MISMATCH" if exists else "BLOCKED_EXPECTED_URN_NOT_FOUND"
        )
        verified = status == "RESOLVED_EXPECTED"
        all_verified = all_verified and verified
        rows.append({
            **{k: item[k] for k in ("model", "relation_name", "platform", "platform_instance", "env", "namespace", "dataset_name", "expected_urn")},
            "status": status,
            "resolved_urn": urn if verified else None,
            "runtime_verified": verified,
        })
    payload = {
        "contract": "commerce_phase7a_dataset_identity_resolution",
        "mode": "EXACT_RUNTIME_ONLY",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": all_verified,
        "identities": rows,
    }
    if not all_verified:
        bad = [f"{x['model']}:{x['status']}" for x in rows if not x["runtime_verified"]]
        raise Phase7DataHubRuntimeError("Exact Dataset identity resolution failed: " + ", ".join(bad))
    if write:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        (EVIDENCE_DIR / "dataset_identity_resolution.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return payload


def _emit_dataset_governance(urn: str, spec: dict[str, Any]) -> None:
    """把一份已批准治理 spec 写到一个 exact Dataset URN。
    
    输入：精确 Dataset URN 与 Domain / Owners / Tags / Terms / Structured Properties。
    DataHub API：set_domain + DatasetPatchBuilder，分别追加 Owner、Tag、GlossaryTerm 和 Structured Property。
    工程边界：调用前必须已经完成 exact identity resolution；本函数不负责猜 Dataset。"""
    try:
        from datahub.emitter.mce_builder import make_group_urn, make_tag_urn, make_term_urn
        from datahub.metadata.schema_classes import GlossaryTermAssociationClass, OwnerClass, OwnershipTypeClass, TagAssociationClass
        from datahub.metadata.urns import DomainUrn
        from datahub.specific.dataset import DatasetPatchBuilder
    except ImportError as exc:
        raise Phase7DataHubRuntimeError("DataHub Dataset governance patch APIs are unavailable") from exc

    client = _sdk_client()
    dataset = client.entities.get(urn)
    dataset.set_domain(DomainUrn(id=spec["domain"]))
    client.entities.update(dataset)

    patch = DatasetPatchBuilder(urn)
    owners = spec["owners"]
    patch.add_owner(OwnerClass(owner=make_group_urn(owners["business"]), type=OwnershipTypeClass.BUSINESS_OWNER))
    patch.add_owner(OwnerClass(owner=make_group_urn(owners["technical"]), type=OwnershipTypeClass.TECHNICAL_OWNER))
    for tag in spec.get("tags", []):
        patch.add_tag(TagAssociationClass(tag=make_tag_urn(tag)))
    for term in spec.get("glossary_terms", []):
        patch.add_term(GlossaryTermAssociationClass(urn=make_term_urn(term)))
    for key, value in spec.get("structured_properties", {}).items():
        patch.add_structured_property(f"urn:li:structuredProperty:{key}", value)
    client.entities.update(patch)


def _governance_expectation(asset: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    """把默认治理策略与单个 asset 覆盖项合并成最终期望治理状态。
    
    输入：asset_policy 中的一项 asset 与 defaults。
    输出：Domain、Owner、去重 Tags、Glossary Terms、Structured Properties。
    工程边界：这里只构造 expected state，不证明 DataHub 已经接受这些 aspect。"""
    return {
        "domain": asset["domain"],
        "owners": dict(policy["defaults"]["owners"]),
        "tags": list(dict.fromkeys([*policy["defaults"].get("tags", []), *asset.get("tags", [])])),
        "glossary_terms": list(asset.get("glossary_terms", [])),
        "structured_properties": {
            **policy["defaults"].get("structured_properties", {}),
            **asset.get("structured_properties", {}),
        },
    }


def apply_and_verify_governance(*, graph: Any | None = None) -> dict[str, Any]:
    """对 exact Dataset 应用治理，并通过最终 re-query 生成 Runtime Evidence。
    
    业务流程：检查写入 gate → exact identity → 写治理 → re-query DataHub aspects → 逐项核 Domain/Owner/Tag/Term/Property/Lineage。
    输出：只有全部资产通过最终 re-query，才写 .runtime/evidence/phase7a/datahub/datahub_runtime.json 并标记 RUNTIME_VERIFIED。
    DataHub API：get_entity_raw() 读取真实 aspects；lineage-required models 还要求至少一个 upstream Dataset。
    工程边界：任一资产未通过就整体 fail closed；源码存在不等于该证据已经生成。"""
    _require_gate("PHASE7A_ALLOW_DATAHUB_GOVERNANCE_WRITE")
    graph = graph or _graph()
    identities_path = EVIDENCE_DIR / "dataset_identity_resolution.json"
    if not identities_path.exists():
        resolve_exact_identities(graph=graph, write=True)
    identities = json.loads(identities_path.read_text(encoding="utf-8"))
    by_model = {x["model"]: x for x in identities["identities"]}
    policy = _yaml("metadata/datahub/governance/asset_policy.yml")

    expectations: dict[str, dict[str, Any]] = {}
    for asset in policy["assets"]:
        identity = by_model.get(asset["model"])
        if not identity or identity.get("status") != "RESOLVED_EXPECTED" or identity.get("resolved_urn") != identity.get("expected_urn"):
            raise Phase7DataHubRuntimeError(f"Governance write blocked: unresolved exact identity for {asset['model']}")
        expected = _governance_expectation(asset, policy)
        expectations[asset["model"]] = expected
        _emit_dataset_governance(identity["resolved_urn"], expected)

    verification_contract = _yaml("infra/contracts/phase7/datahub_runtime_verification.yml")
    lineage_required = set(verification_contract["lineage"]["required_models"])
    assets = []
    all_verified = True
    for model, expected in expectations.items():
        identity = by_model[model]
        urn = identity["resolved_urn"]
        raw = graph.get_entity_raw(
            urn,
            aspects=["dataPlatformInstance", "domains", "ownership", "globalTags", "glossaryTerms", "structuredProperties", "upstreamLineage"],
        )
        strings = _collect_strings(raw)
        text = "\n".join(strings)
        checks = {
            "platform_instance": identity["platform_instance"] in text,
            "domain": f"urn:li:domain:{expected['domain']}" in text,
            "business_owner": f"urn:li:corpGroup:{expected['owners']['business']}" in text,
            "technical_owner": f"urn:li:corpGroup:{expected['owners']['technical']}" in text,
            "tags": all(f"urn:li:tag:{tag}" in text for tag in expected["tags"]),
            "glossary_terms": all(f"urn:li:glossaryTerm:{term}" in text for term in expected["glossary_terms"]),
            "structured_properties": all(
                f"urn:li:structuredProperty:{key}" in text and str(value) in text
                for key, value in expected["structured_properties"].items()
            ),
        }
        if model in lineage_required:
            upstream_urns = {s for s in strings if s.startswith("urn:li:dataset:") and s != urn}
            checks["lineage"] = len(upstream_urns) >= int(verification_contract["lineage"]["minimum_upstream_relationships"])
        passed = all(checks.values())
        all_verified = all_verified and passed
        assets.append({
            "model": model,
            "status": "RUNTIME_VERIFIED" if passed else "BLOCKED_RUNTIME_REQUERY",
            "identity": {
                "status": identity["status"],
                "expected_urn": identity["expected_urn"],
                "resolved_urn": identity["resolved_urn"],
            },
            "checks": checks,
        })

    if not all_verified:
        bad = [x["model"] for x in assets if x["status"] != "RUNTIME_VERIFIED"]
        raise Phase7DataHubRuntimeError("Final DataHub governance re-query failed for: " + ", ".join(bad))

    payload = {
        "contract": "commerce_phase7a_datahub_runtime",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": True,
        "status": "DATAHUB_METADATA_PLANE_VERIFIED",
        "assets": assets,
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "datahub_runtime.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


def main() -> int:
    """Phase 7A DataHub Runtime CLI 入口。
    
    命令：bootstrap-definitions / resolve-identities / apply-and-verify-governance。
    输出：成功时打印 JSON；Phase7DataHubRuntimeError 时返回 exit code 2。
    工程边界：CLI 只是可执行入口，只有真实运行产生 .runtime evidence 后才能声明 Runtime PASS。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["bootstrap-definitions", "resolve-identities", "apply-and-verify-governance"])
    args = parser.parse_args()
    try:
        if args.command == "bootstrap-definitions":
            payload = bootstrap_governance_definitions()
        elif args.command == "resolve-identities":
            payload = resolve_exact_identities()
        else:
            payload = apply_and_verify_governance()
    except Phase7DataHubRuntimeError as exc:
        print(str(exc))
        return 2
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
