"""
把本地 Shopify Fixture 写入 Raw Iceberg。

Raw 的 Grain：一次 API Observation（一条 API 观测）。
Dagster 传入 effective source window 后，只读取 updatedAt 落在 [start, end) 的 fixture。
没有传窗口参数时保留原来的“读取全部本地 fixture”开发行为。
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import Row, SparkSession

from source_window import SourceWindow


FIXTURE_DIR = Path("data/fixtures/shopify")
RAW_TABLE = "polaris.raw.raw_shopify_order_payload"


def utc_now():
    """返回带 UTC 时区的当前时间。
    
    输出：timezone-aware ``datetime``。
    用途：给 Fixture Observation 生成技术观察时间，避免 naive datetime 带来时区歧义。
    """
    return datetime.now(timezone.utc)


def parse_args() -> argparse.Namespace:
    """解析 Fixture Raw Loader 的命令行参数。
    
    输出：Fixture 目录、Catalog 配置与可选 Source Window。
    工程边界：参数只决定测试输入和窗口，不改变 Raw Observation 契约。
    """
    parser = argparse.ArgumentParser(description="把 Shopify Fixture 写入 Raw Iceberg")
    parser.add_argument(
        "--window-start",
        help="有效源读取开始时间，包含边界，ISO-8601 UTC",
    )
    parser.add_argument(
        "--window-end",
        help="有效源读取结束时间，不包含边界，ISO-8601 UTC",
    )
    return parser.parse_args()


def fixture_in_window(payload_obj: dict, source_window: SourceWindow | None) -> bool:
    """判断一个 Fixture Order 是否落在本次 Source Read Window。
    
    输入：Fixture JSON 与可选窗口。
    输出：bool。
    时间语义：按 Shopify ``updatedAt`` 判断，与 Production API 的 updated_at 增量语义保持一致。
    """
    if source_window is None:
        return True
    updated_at = payload_obj.get("updatedAt")
    if not updated_at:
        raise ValueError(
            f"fixture is missing Shopify updatedAt: {payload_obj.get('id', '<unknown>')}"
        )
    return source_window.contains(updated_at)


def select_fixture_paths(source_window: SourceWindow | None) -> list[Path]:
    """选择本次窗口需要写入 Raw 的 Fixture 文件。
    
    输入：可选 SourceWindow。
    输出：按稳定顺序排列的 JSON 文件路径列表。
    工程目的：Fixture 与 Production 共用同一窗口语义，使 Clean-room Acceptance 可重复。
    """
    selected: list[Path] = []
    for file_path in sorted(FIXTURE_DIR.glob("*.json")):
        payload_obj = json.loads(file_path.read_text(encoding="utf-8"))
        if fixture_in_window(payload_obj, source_window):
            selected.append(file_path)
    return selected


def main():
    """把选中的 Fixture Order 作为 Raw Observation 追加写入 Iceberg。
    
    输入：本地 Fixture JSON + Source Window。
    输出：Raw Shopify Observation。
    工程边界：Fixture 是测试输入，不代表真实 Shopify API Runtime 已执行。
    """
    args = parse_args()
    source_window = SourceWindow.from_cli_values(args.window_start, args.window_end)

    spark = SparkSession.builder.appName("load-shopify-fixtures").getOrCreate()

    batch_id = str(uuid.uuid4())
    extracted_at = utc_now()
    selected_paths = select_fixture_paths(source_window)

    rows = []
    for file_path in selected_paths:
        payload_obj = json.loads(file_path.read_text(encoding="utf-8"))
        rows.append(
            Row(
                shopify_order_id=payload_obj["id"],
                order_updated_at=payload_obj.get("updatedAt"),
                extracted_at=extracted_at,
                batch_id=batch_id,
                source_file=file_path.name,
                payload=json.dumps(payload_obj, ensure_ascii=False),
            )
        )

    # 空窗口是合法结果：当天可能确实没有任何模拟 Observation。
    # Python / Spark 细节：不要调用 createDataFrame([])，因为 Spark 无法从空列表推断 Schema。
    if not rows:
        print(
            "loaded 0 fixture rows; source window contains no local demo observations "
            f"window_start={args.window_start} window_end={args.window_end}"
        )
        return

    spark.createDataFrame(rows).writeTo(RAW_TABLE).append()

    print(
        f"loaded {len(rows)} fixture rows, batch_id={batch_id}, "
        f"window_start={args.window_start}, window_end={args.window_end}"
    )


if __name__ == "__main__":
    main()
