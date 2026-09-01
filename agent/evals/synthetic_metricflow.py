"""Synthetic MetricFlow Runner（合成 MetricFlow 运行器）。

这是 Eval-only adapter，不属于生产 Tool Surface。

它模拟 MetricFlow CLI 的“Explain -> CSV Query”形态，但计算只覆盖
Runtime Golden Fixture 中明确允许的两个简单指标：
- gross_sales = sum(gross_sales_amount)
- order_count = count_distinct(order_id)

这两个定义与当前受治理 Semantic Contract 对齐。
任何额外 Metric / Dimension 一律 Fail Closed，避免测试运行器悄悄变成第二套语义层。
"""

from __future__ import annotations

import csv
import re
import subprocess
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any


_FILTER_RE = re.compile(
    r"\{\{\s*Dimension\('([^']+)'\)\s*\}\}\s*=\s*'([^']*)'"
)


class SyntheticMetricFlowRunner:
    """读取 repository-owned fixture 并模拟受限 MetricFlow CLI。"""

    ALLOWED_METRICS = {"gross_sales", "order_count"}
    ALLOWED_FILTERS = {"store__region", "store__country"}
    ALLOWED_GROUP_BY = {"metric_time__day"}

    def __init__(self, fixture_path: Path | str):
        self.fixture_path = Path(fixture_path).resolve()
        self.calls: list[list[str]] = []
        with self.fixture_path.open(newline="", encoding="utf-8") as handle:
            self.rows = list(csv.DictReader(handle))

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))

        if "--explain" in cmd:
            error = self._validate_command(cmd)
            return subprocess.CompletedProcess(
                cmd,
                1 if error else 0,
                stdout="" if error else "SYNTHETIC_PLAN_OK",
                stderr=error or "",
            )

        if "--csv" not in cmd:
            return subprocess.CompletedProcess(
                cmd,
                2,
                stdout="",
                stderr="Synthetic runner only supports --explain or --csv query.",
            )

        error = self._validate_command(cmd)
        if error:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=error)

        csv_path = Path(cmd[cmd.index("--csv") + 1])
        result_rows, columns = self._query(cmd)
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(result_rows)

        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="SYNTHETIC_QUERY_OK",
            stderr="",
        )

    def _validate_command(self, cmd: list[str]) -> str | None:
        metrics = self._csv_arg(cmd, "--metrics")
        unknown_metrics = sorted(set(metrics) - self.ALLOWED_METRICS)
        if unknown_metrics:
            return "Synthetic golden runner does not support metric(s): " + ", ".join(unknown_metrics)

        group_by = self._csv_arg(cmd, "--group-by")
        unknown_group = sorted(set(group_by) - self.ALLOWED_GROUP_BY)
        if unknown_group:
            return "Synthetic golden runner does not support group_by: " + ", ".join(unknown_group)

        for expression in self._multi_arg(cmd, "--where"):
            match = _FILTER_RE.fullmatch(expression)
            if match is None or match.group(1) not in self.ALLOWED_FILTERS:
                return f"Unsupported synthetic governed filter: {expression}"

        return None

    def _query(self, cmd: list[str]) -> tuple[list[dict[str, str]], list[str]]:
        metrics = self._csv_arg(cmd, "--metrics")
        group_by = self._csv_arg(cmd, "--group-by")
        start = self._arg(cmd, "--start-time")[:10]
        end = self._arg(cmd, "--end-time")[:10]

        filters: dict[str, str] = {}
        for expression in self._multi_arg(cmd, "--where"):
            match = _FILTER_RE.fullmatch(expression)
            assert match is not None
            filters[match.group(1)] = match.group(2).replace("''", "'")

        rows = [
            row
            for row in self.rows
            if start <= row["metric_time__day"] <= end
            and all(row.get(dimension) == value for dimension, value in filters.items())
        ]

        if group_by == ["metric_time__day"]:
            grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
            for row in rows:
                grouped[row["metric_time__day"]].append(row)
            output = []
            for day in sorted(grouped):
                output.append(
                    {
                        "metric_time__day": day,
                        **self._aggregate(grouped[day], metrics),
                    }
                )
            columns = ["metric_time__day", *metrics]
        elif not group_by:
            output = [self._aggregate(rows, metrics)]
            columns = list(metrics)
        else:
            raise AssertionError(f"unexpected governed group_by: {group_by}")

        limit = int(self._arg(cmd, "--limit"))
        return output[:limit], columns

    @staticmethod
    def _aggregate(rows: list[dict[str, str]], metrics: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for metric in metrics:
            if metric == "gross_sales":
                value = sum((Decimal(row["gross_sales_amount"]) for row in rows), Decimal("0"))
                result[metric] = f"{value:.2f}"
            elif metric == "order_count":
                result[metric] = str(len({row["order_id"] for row in rows}))
            else:
                raise AssertionError(f"unsupported metric: {metric}")
        return result

    @staticmethod
    def _arg(cmd: list[str], name: str) -> str:
        if name not in cmd:
            return ""
        return str(cmd[cmd.index(name) + 1])

    @classmethod
    def _csv_arg(cls, cmd: list[str], name: str) -> list[str]:
        raw = cls._arg(cmd, name)
        return [item for item in raw.split(",") if item]

    @staticmethod
    def _multi_arg(cmd: list[str], name: str) -> list[str]:
        output: list[str] = []
        for index, token in enumerate(cmd):
            if token == name and index + 1 < len(cmd):
                output.append(str(cmd[index + 1]))
        return output
