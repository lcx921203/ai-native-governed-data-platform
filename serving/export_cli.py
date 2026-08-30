"""手工/验收入口：执行一个 Serving Contract 的 MetricFlow Export，并可调用 Spark 完成 Iceberg 物化。"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from serving.contracts import load_serving_contract
from serving.exporter import ExportStatus, MetricFlowServingExporter


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    """解析人工验收参数；只允许选择 Git 中已有 Contract 和一个业务日，不接受任意 Metric/SQL。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="serving/contracts/bi_daily_executive.yml")
    parser.add_argument("--partition-key", required=True)
    parser.add_argument("--materialize", action="store_true")
    return parser.parse_args()


def main() -> None:
    """执行 Contract Export；只有显式 ``--materialize`` 时才调用容器内 Spark Writer。

    输入：Contract 路径 + YYYY-MM-DD partition。输出：CSV；可选继续物化 Iceberg。
    工程边界：MetricFlow Export 非 COMPLETE 时立即以非零状态退出，绝不带病写 Serving。
    """
    args = parse_args()
    contract_path = ROOT / args.contract
    contract = load_serving_contract(contract_path)
    result = MetricFlowServingExporter(ROOT).export(contract, args.partition_key)
    print(f"export_status={result.status.value} message={result.message}")
    if result.status is not ExportStatus.COMPLETE or result.csv_path is None:
        raise SystemExit(2)

    print(f"csv={result.csv_path.relative_to(ROOT)}")
    if not args.materialize:
        return

    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "spark-thrift",
        "/opt/spark/bin/spark-submit",
        "/opt/project/serving/jobs/materialize_export.py",
        "--contract",
        str(contract_path.relative_to(ROOT)),
        "--csv",
        str(result.csv_path.relative_to(ROOT)),
        "--partition-key",
        args.partition_key,
    ]
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
