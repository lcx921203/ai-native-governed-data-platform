#!/usr/bin/env python3
"""执行 MetricFlow Golden Query，并把 CSV 结果与人工维护的 Golden Oracle 做确定性比较。

这份程序验证“同一受治理查询是否返回预期结果”，但不会把静态 Oracle 或 Comparator
冒充成真实 MetricFlow Runtime Evidence。真实 ``mf query`` 是否运行成功仍需要独立日志。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "tests/expected/commerce_metrics.yml"


def sha256(path: Path) -> str:
    """计算 Fixture 文件 SHA-256。
    
    输入：文件路径。
    输出：十六进制哈希。
    工程目的：Golden Oracle 必须绑定固定输入，避免 Fixture 漂移后仍拿旧 expected 结果做“假通过”。
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_fixtures(contract: dict) -> None:
    """校验 Golden Contract 声明的 Fixture 哈希。
    
    输入：commerce_metrics.yml。
    输出：全部匹配时无返回；任一漂移立即失败。
    工程边界：先证明输入没变，后面的 Metric 比较才有意义。
    """
    for rel_path, expected in contract["fixture_sha256"].items():
        actual = sha256(ROOT / rel_path)
        if actual != expected:
            raise AssertionError(
                f"Fixture drift detected: {rel_path}\nexpected={expected}\nactual={actual}"
            )


def normalized_key(value: str) -> str:
    """把字段名标准化为稳定比较 key。
    
    输入：字符串。
    输出：去除两端空白后的规范 key。
    """
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def as_decimal(value: object) -> Decimal | None:
    """在可能情况下把结果值转换成 Decimal。
    
    输入：CSV/expected 中的任意标量。
    输出：Decimal 或 None。
    工程目的：金额/比率比较避免直接使用浮点误差语义。
    """
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def equal_value(expected: object, actual: object) -> bool:
    """按 Golden Acceptance 规则比较一个 expected 与 actual 标量。
    
    输入：两个标量。
    输出：bool。
    比较语义：数值优先 Decimal 比较，其他值按规范字符串比较。
    """
    left = as_decimal(expected)
    right = as_decimal(actual)
    if left is not None and right is not None:
        return left == right
    return str(expected) == str(actual)


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    """规范化 MetricFlow CSV 的一行结果。
    
    输入：CSV DictReader 行。
    输出：去除 key/value 多余空白后的 dict。
    """
    return {normalized_key(k): v.strip() for k, v in row.items()}


def compare_rows(query: dict, actual_rows: list[dict[str, str]]) -> None:
    """比较一条 Golden Query 的实际结果集与 expected rows。
    
    输入：Query Contract 与 MetricFlow CSV rows。
    输出：完全一致时无返回；行数、主键或指标值不一致时失败。
    工程边界：Comparator 只判结果契约，不替代 MetricFlow Runtime 本身的运行证据。
    """
    expected_rows = [normalize_row({k: str(v) for k, v in r.items()}) for r in query["expected"]]
    actual_rows = [normalize_row(r) for r in actual_rows]
    dimensions = [normalized_key(x) for x in query.get("group_by", [])]
    metrics = [normalized_key(x) for x in query["metrics"]]

    def row_id(row: dict[str, str]) -> tuple[str, ...]:
        """根据 Golden Query 的 dimensions 生成一行稳定标识。
        
        输入：规范化结果行。
        输出：由维度值组成的 tuple；无维度时返回单一 global key。
        Python 语法：``tuple[str, ...]`` 表示长度可变、元素均为 str 的元组，不是省略代码。
        """
        return tuple(row.get(dim, "")[:10] if dim.startswith("metric_time") else row.get(dim, "") for dim in dimensions)

    expected_by_id = {row_id(row): row for row in expected_rows}
    actual_by_id = {row_id(row): row for row in actual_rows}
    if set(expected_by_id) != set(actual_by_id):
        raise AssertionError(
            f"{query['name']}: row keys differ\nexpected={sorted(expected_by_id)}\nactual={sorted(actual_by_id)}"
        )

    for key, expected in expected_by_id.items():
        actual = actual_by_id[key]
        for metric in metrics:
            if metric not in actual:
                raise AssertionError(f"{query['name']}: missing metric column {metric}")
            if not equal_value(expected[metric], actual[metric]):
                raise AssertionError(
                    f"{query['name']} {key} {metric}: expected {expected[metric]}, got {actual[metric]}"
                )


def query_command(query: dict, csv_path: Path) -> list[str]:
    """根据 Golden Query Contract 生成一条 MetricFlow CLI 命令。
    
    输入：指标/维度/过滤条件与输出 CSV 路径。
    输出：argv list。
    工程边界：这里只构造命令，不宣称 MetricFlow 已执行成功。
    """
    cmd = ["mf", "query", "--metrics", ",".join(query["metrics"])]
    if query.get("group_by"):
        cmd += ["--group-by", ",".join(query["group_by"])]
    cmd += ["--limit", "1000", "--csv", str(csv_path)]
    return cmd


def main() -> None:
    """按 Golden Contract 依次执行/比较 MetricFlow 查询结果。
    
    流程：校验 Fixture SHA-256 → 运行每条受治理 Query → 读取 CSV → 与 Oracle 比较。
    输出：全部通过时退出 0。
    工程边界：只有真实 CLI 被执行且所有比较通过，才能称 MetricFlow Runtime Acceptance PASS。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".runtime/golden-metrics")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    contract = yaml.safe_load(args.contract.read_text())
    verify_fixtures(contract)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for query in contract["queries"]:
        csv_path = args.output_dir / f"{query['name']}.csv"
        cmd = query_command(query, csv_path)
        print("+", " ".join(cmd))
        if args.dry_run:
            continue
        subprocess.run(cmd, check=True, cwd=ROOT / "dbt/mercaso_metricflow_compat")
        with csv_path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        compare_rows(query, rows)
        print(f"PASS {query['name']}")

    print(f"Golden acceptance contract validated: {len(contract['queries'])} queries")


if __name__ == "__main__":
    main()
