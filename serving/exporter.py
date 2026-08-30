"""固定 Serving Contract 的 MetricFlow Export Runner。

业务逻辑：Dagster 给出一个业务日分区后，本模块只按 Git 管理的 Serving Contract 调用 MetricFlow，
把受治理 Metric 结果落成临时 CSV；随后由 Spark/Iceberg 物化器写入 Serving Table。
输入：ServingContract + partition_key；输出：结构已校验的 CSV Artifact。
工程边界：不生成任意 SQL、不接受用户自定义过滤；Runtime gate 默认关闭，未显式开放时 Fail Closed。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
import os
from pathlib import Path
import subprocess
from typing import Callable

from .contracts import ServingContract


Runner = Callable[..., subprocess.CompletedProcess[str]]


class ExportStatus(str, Enum):
    """固定指标导出的受控状态；只有 COMPLETE 允许继续写 Iceberg。"""

    COMPLETE = "COMPLETE"
    DEFERRED = "DEFERRED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ServingExportResult:
    """一次固定 Metric Export 的结果；只有 COMPLETE 才允许进入 Iceberg 物化。"""

    status: ExportStatus
    contract_name: str
    partition_key: str
    csv_path: Path | None = None
    message: str = ""


class MetricFlowServingExporter:
    """把 Serving Contract 翻译成受限 MetricFlow CLI 调用。

    复用项目现有 ``mercaso_metricflow_compat`` 运行面；Serving 只消费 MetricFlow 结果，不拥有指标公式。
    """

    def __init__(self, project_root: Path | str, runner: Runner | None = None):
        self.root = Path(project_root).resolve()
        self.runner = runner or subprocess.run

    def export(self, contract: ServingContract, partition_key: str) -> ServingExportResult:
        """执行一个业务日的固定指标导出并校验 CSV Header。

        Runtime gate ``SERVING_ALLOW_METRIC_EXPORT`` 默认为 false；防止静态定义被误写成已执行事实。
        """

        try:
            day = date.fromisoformat(partition_key)
        except ValueError as exc:
            return ServingExportResult(
                ExportStatus.ERROR,
                contract.name,
                partition_key,
                message=f"invalid daily partition key: {exc}",
            )

        if os.getenv("SERVING_ALLOW_METRIC_EXPORT", "false").lower() != "true":
            return ServingExportResult(
                ExportStatus.DEFERRED,
                contract.name,
                partition_key,
                message="Metric export is disabled; set SERVING_ALLOW_METRIC_EXPORT=true in the intended runtime.",
            )

        mf = self._metricflow_binary()
        if not mf.exists():
            return ServingExportResult(
                ExportStatus.DEFERRED,
                contract.name,
                partition_key,
                message=f"MetricFlow CLI not found at {mf}",
            )

        project_dir = self.root / "dbt" / "mercaso_metricflow_compat"
        generated_spec = project_dir / "models" / "_generated_semantic_legacy.yml"
        if not generated_spec.exists():
            return ServingExportResult(
                ExportStatus.DEFERRED,
                contract.name,
                partition_key,
                message="generated MetricFlow compatibility semantic spec is missing",
            )

        out_dir = self.root / ".runtime" / "serving" / contract.name / partition_key
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path = out_dir / "metricflow.csv"
        if csv_path.exists():
            csv_path.unlink()

        start_time = day.isoformat()
        end_time = (day + timedelta(days=1)).isoformat()
        command = [
            str(mf),
            *contract.metricflow_args(
                start_time=start_time,
                end_time=end_time,
                csv_path=csv_path,
            ),
        ]
        env = os.environ.copy()
        env["DBT_PROFILES_DIR"] = str(project_dir)
        timeout = int(os.getenv("SERVING_METRICFLOW_TIMEOUT_SECONDS", "900"))

        try:
            result = self.runner(
                command,
                cwd=str(project_dir),
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ServingExportResult(
                ExportStatus.ERROR,
                contract.name,
                partition_key,
                message=f"MetricFlow export exceeded {timeout}s",
            )

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "MetricFlow export failed").strip()
            return ServingExportResult(
                ExportStatus.ERROR,
                contract.name,
                partition_key,
                message=message[-1500:],
            )
        if not csv_path.exists():
            return ServingExportResult(
                ExportStatus.ERROR,
                contract.name,
                partition_key,
                message="MetricFlow reported success but CSV artifact is missing",
            )

        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle)
                header = next(reader)
        except (OSError, StopIteration) as exc:
            return ServingExportResult(
                ExportStatus.ERROR,
                contract.name,
                partition_key,
                message=f"invalid MetricFlow CSV artifact: {exc}",
            )

        missing = sorted(set(contract.expected_metricflow_columns) - set(header))
        if missing:
            return ServingExportResult(
                ExportStatus.ERROR,
                contract.name,
                partition_key,
                csv_path=csv_path,
                message=f"MetricFlow CSV missing expected columns: {missing}",
            )

        return ServingExportResult(
            ExportStatus.COMPLETE,
            contract.name,
            partition_key,
            csv_path=csv_path,
            message="MetricFlow fixed serving export completed",
        )

    def _metricflow_binary(self) -> Path:
        """解析 Serving 使用的 MetricFlow CLI；默认复用项目 ``.venv-mf``。"""

        configured = os.getenv("SERVING_METRICFLOW_BIN", "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return (self.root / ".venv-mf" / "bin" / "mf").resolve()
