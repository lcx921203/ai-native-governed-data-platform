"""在 OpenAPI ingestion 后记录 Serving API Endpoint 的精确 DataHub Dataset Identity。

操作者必须提供从 DataHub 实体复制的 exact Dataset URN；工具只做存在性、Path、HTTP Method 三重验证。
工程边界：禁止 search / fuzzy bind / 自造 URN；通过后只把 Runtime Evidence 写入 ``.runtime/``。
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
REGISTRY = ROOT / "metadata/datahub/governance/consumer_registry.yml"
OUT = ROOT / ".runtime/evidence/serving/datahub/api_endpoint_identities.json"


class ConsumerIdentityError(RuntimeError):
    """API Consumer 身份不能被精确证明时抛出的 Fail-Closed 异常。"""

    pass


def _graph():
    """创建只用于 exact entity read 的 DataHubGraph；连接信息来自环境变量。"""
    try:
        from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
    except ImportError as exc:
        raise ConsumerIdentityError("acryl-datahub graph client is required") from exc
    return DataHubGraph(
        DatahubClientConfig(
            server=os.getenv("DATAHUB_GMS_URL", "http://localhost:8080"),
            token=os.getenv("DATAHUB_GMS_TOKEN"),
        )
    )


def _collect_strings(value: Any) -> list[str]:
    """递归展开 DataHub Raw Aspect 中的文本，用于验证 Endpoint Path / Method 证据。"""
    if isinstance(value, dict):
        out: list[str] = []
        for k, v in value.items():
            out.extend(_collect_strings(k)); out.extend(_collect_strings(v))
        return out
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_collect_strings(item))
        return out
    return [] if value is None else [str(value)]


def _endpoint_contracts() -> dict[str, dict[str, Any]]:
    """读取 Git 中固定的 API Consumer Registry，以 Endpoint ID 为键返回期望合同。"""
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    return {x["id"]: x for x in registry["api"]["endpoints"]}


def verify_and_record(pairs: list[str], *, graph: Any | None = None) -> dict[str, Any]:
    """验证 ``endpoint_id=exact_urn`` 列表，并生成 Runtime-only Identity Evidence。

    所有 Registry Endpoint 必须一次性提供；任何缺失、实体不存在、Path/Method 不匹配都会整体失败。
    """
    graph = graph or _graph()
    expected = _endpoint_contracts()
    supplied: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ConsumerIdentityError("--endpoint must use id=exact_urn")
        endpoint_id, urn = pair.split("=", 1)
        if endpoint_id not in expected:
            raise ConsumerIdentityError(f"Unknown endpoint id: {endpoint_id}")
        supplied[endpoint_id] = urn
    if set(supplied) != set(expected):
        raise ConsumerIdentityError(f"Exact endpoint URNs required for: {sorted(expected)}")

    rows = []
    for endpoint_id, spec in expected.items():
        urn = supplied[endpoint_id]
        if not urn.startswith("urn:li:dataset:"):
            raise ConsumerIdentityError(f"Endpoint {endpoint_id} must resolve to a DataHub Dataset URN")
        if not graph.exists(urn):
            raise ConsumerIdentityError(f"Endpoint Dataset does not exist: {urn}")
        raw = graph.get_entity_raw(urn, aspects=["datasetProperties", "schemaMetadata", "subTypes"])
        text = "\n".join(_collect_strings(raw))
        path_ok = spec["path"] in text
        method_ok = spec["method"].lower() in text.lower()
        if not path_ok or not method_ok:
            raise ConsumerIdentityError(
                f"Exact URN does not prove endpoint contract {endpoint_id}: path_ok={path_ok}, method_ok={method_ok}"
            )
        rows.append({
            "id": endpoint_id,
            "method": spec["method"],
            "path": spec["path"],
            "resolved_urn": urn,
            "runtime_verified": True,
            "checks": {"entity_exists": True, "path": True, "method": True},
        })

    payload = {
        "contract": "commerce_serving_api_endpoint_identity_resolution",
        "mode": "OPERATOR_SUPPLIED_EXACT_URN",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": True,
        "endpoints": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    """CLI 入口：接收重复 ``--endpoint id=exact_urn``，验证成功返回 0，拒绝返回 2。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", action="append", default=[], help="endpoint_id=exact_DataHub_Dataset_URN")
    args = parser.parse_args()
    try:
        payload = verify_and_record(args.endpoint)
    except ConsumerIdentityError as exc:
        print(str(exc))
        return 2
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
