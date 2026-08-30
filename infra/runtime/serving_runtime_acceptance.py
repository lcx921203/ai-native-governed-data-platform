"""Serving Layer 真实运行验收：MetricFlow Export → Iceberg → Trino → FastAPI。

本程序只在显式 Runtime Gate 打开后运行。它不会启动服务或生成业务数据；前置脚本应先完成
Serving Export。验收通过后写 ``.runtime/evidence/serving/serving_runtime.json``，供最终闭环聚合器读取。
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".runtime/evidence/serving/serving_runtime.json"


class ServingRuntimeAcceptanceError(RuntimeError):
    """Serving E2E 的 Gate、Trino 查询、Iceberg Snapshot 或 API 对账失败时抛出的受控异常。"""


def _require_gate() -> None:
    """要求 ``SERVING_ALLOW_RUNTIME_ACCEPTANCE=true``；避免本地静态检查被误升级成 Runtime PASS。"""
    if os.getenv("SERVING_ALLOW_RUNTIME_ACCEPTANCE", "false").lower() != "true":
        raise ServingRuntimeAcceptanceError(
            "REFUSED: set SERVING_ALLOW_RUNTIME_ACCEPTANCE=true explicitly"
        )


def _trino_scalar(sql: str) -> int:
    """通过 Compose 内 Trino CLI 执行只读标量 SQL，并解析唯一整数结果。

    Query Engine 只验证 Serving Table / Iceberg Metadata；这里不定义任何 Metric 公式。
    """
    command = [
        "docker", "compose", "exec", "-T", "trino", "trino",
        "--server", "http://localhost:8080",
        "--output-format", "TSV",
        "--execute", sql,
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ServingRuntimeAcceptanceError(
            f"Trino acceptance query failed: {completed.stderr.strip()}"
        )
    lines = [line.strip().strip('"') for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise ServingRuntimeAcceptanceError("Trino acceptance query returned no scalar output")
    try:
        return int(lines[-1])
    except ValueError as exc:
        raise ServingRuntimeAcceptanceError(
            f"Trino scalar output is not an integer: {lines[-1]!r}"
        ) from exc


def _get_json(url: str) -> Any:
    """读取固定 Serving API JSON；HTTP 非 2xx、超时或非法 JSON 都直接失败。"""
    with urlopen(url, timeout=10) as response:  # noqa: S310 - URL is repository-controlled localhost contract.
        if response.status < 200 or response.status >= 300:
            raise ServingRuntimeAcceptanceError(f"HTTP acceptance failed: {url} -> {response.status}")
        return json.loads(response.read().decode("utf-8"))


def verify(partition_key: str) -> dict[str, Any]:
    """对一个业务日执行 Trino ↔ FastAPI 对账并生成 Runtime Evidence Payload。

    验证：Serving Table 可查、Iceberg 至少存在 Snapshot、API Ready、API 行数与 Trino 行数一致、
    每行 business_date 与目标分区一致。0 行业务结果允许存在，但 Snapshot 必须证明物化发生过。
    """
    _require_gate()
    partition_day = date.fromisoformat(partition_key)
    table = "iceberg.serving.bi_daily_executive"

    trino_rows = _trino_scalar(
        f"SELECT count(*) FROM {table} WHERE business_date = DATE '{partition_day.isoformat()}'"
    )
    snapshot_count = _trino_scalar(
        'SELECT count(*) FROM iceberg.serving."bi_daily_executive$snapshots"'
    )
    if snapshot_count < 1:
        raise ServingRuntimeAcceptanceError("Iceberg Serving table has no snapshot evidence")

    port = os.getenv("SERVING_API_PORT", "8081")
    base = f"http://localhost:{port}"
    ready = _get_json(f"{base}/health/ready")
    if ready != {"status": "ready"}:
        raise ServingRuntimeAcceptanceError(f"Serving API readiness mismatch: {ready!r}")

    rows = _get_json(f"{base}/api/v1/executive/daily?business_date={partition_day.isoformat()}")
    if not isinstance(rows, list):
        raise ServingRuntimeAcceptanceError("Executive API response must be a JSON list")
    if len(rows) != trino_rows:
        raise ServingRuntimeAcceptanceError(
            f"Trino/API row-count mismatch: trino={trino_rows}, api={len(rows)}"
        )
    required = {
        "business_date", "region", "gross_sales", "sales_before_reversal",
        "net_sales", "order_count", "average_order_value",
    }
    for row in rows:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ServingRuntimeAcceptanceError(f"Serving API row violates response contract: {row!r}")
        if row["business_date"] != partition_day.isoformat():
            raise ServingRuntimeAcceptanceError(
                f"Serving API row escaped target partition: {row['business_date']!r}"
            )

    return {
        "contract": "commerce_serving_runtime_acceptance",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": True,
        "status": "SERVING_RUNTIME_VERIFIED",
        "partition_key": partition_day.isoformat(),
        "checks": {
            "trino_serving_query": True,
            "iceberg_snapshot": True,
            "fastapi_ready": True,
            "trino_api_row_count_equal": True,
            "api_partition_exact": True,
        },
        "observations": {
            "trino_row_count": trino_rows,
            "api_row_count": len(rows),
            "iceberg_snapshot_count": snapshot_count,
        },
    }


def main() -> int:
    """CLI：验收指定 YYYY-MM-DD 分区并把成功证据写入 ``.runtime``。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--partition-key", required=True)
    args = parser.parse_args()
    try:
        payload = verify(args.partition_key)
    except (ServingRuntimeAcceptanceError, ValueError) as exc:
        print(str(exc))
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
