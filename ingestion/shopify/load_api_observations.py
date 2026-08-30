"""把真实 Shopify API 的 Order Observation 从 JSONL Landing 写入 Raw Iceberg。

真实 HTTP 请求运行在 Dagster Python 进程中；这个 Spark 适配器只负责 Lakehouse 写入。
Fixture 和 Production 因此保持同一 Raw 契约：

- Grain（粒度）= one API observation（一次 API 观察）；
- Write Semantics（写入语义）= append-only / at-least-once（仅追加 / 至少一次）。
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import Row, SparkSession


RAW_TABLE = "polaris.raw.raw_shopify_order_payload"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="把 Shopify API Observation 写入 Raw Iceberg")
    parser.add_argument(
        "--input-file",
        required=True,
        help="相对于项目根目录的 JSONL Landing 文件",
    )
    return parser.parse_args()


def main() -> None:
    """执行 Production Shopify API 抽取，并把完整 Observation 追加到 Raw Iceberg。
    
    输入：Source Window、Shopify 环境密钥/配置、Polaris/Iceberg Runtime。
    输出：Raw Shopify Order Observation。
    工程分层：HTTP/GraphQL 先在 Host 完成，随后落 JSONL，再由 Spark 写 Lakehouse，避免把 Token/HTTP 依赖塞进 Spark 容器。
    工程边界：真实 API 调用和 Spark 写入是否成功必须由 Runtime Evidence 证明。
    """
    args = parse_args()
    input_path = Path(args.input_file)

    # builder.appName(...).getOrCreate()：有 SparkSession 就复用，没有就创建。
    spark = SparkSession.builder.appName("load-shopify-api-observations").getOrCreate()

    # 一个采集批次共用 batch_id 和 extracted_at，便于追溯“这一批是什么时候被平台看到的”。
    batch_id = str(uuid.uuid4())
    extracted_at = datetime.now(timezone.utc)
    rows = []

    # with：上下文管理器。代码块结束后文件会自动关闭，即使中间抛异常也一样。
    with input_path.open(encoding="utf-8") as handle:
        # enumerate(..., start=1)：遍历每行的同时得到从 1 开始的行号，报错时更容易定位。
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue

            payload_obj = json.loads(line)
            order_id = payload_obj.get("id")
            updated_at = payload_obj.get("updatedAt")

            # Raw Observation 至少要有业务对象 ID 和源端更新时间，否则无法稳定追溯。
            if not order_id or not updated_at:
                raise ValueError(
                    f"API Observation 第 {line_number} 行缺少 id / updatedAt"
                )

            rows.append(
                Row(
                    shopify_order_id=order_id,
                    order_updated_at=updated_at,
                    extracted_at=extracted_at,
                    batch_id=batch_id,
                    source_file="shopify-admin-graphql",
                    # ensure_ascii=False：中文等 Unicode 字符保持可读，不转成 \\uXXXX。
                    payload=json.dumps(payload_obj, ensure_ascii=False),
                )
            )

    # 合法的增量窗口可能一个订单更新都没有。
    # 不能 createDataFrame([])，因为 Spark 无法从空列表推断 Schema。
    if not rows:
        print(f"从 {input_path} 读取到 0 条 Shopify API Observation")
        return

    # Raw 只追加 Observation，不在这里按 order_id 去重。
    spark.createDataFrame(rows).writeTo(RAW_TABLE).append()
    print(
        f"写入 {len(rows)} 条 Shopify API Observation, batch_id={batch_id}, "
        f"input_file={input_path}"
    )


if __name__ == "__main__":
    main()
