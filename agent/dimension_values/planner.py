"""Build a bounded governed plan for MetricFlow dimension-value discovery."""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Iterable

import yaml

from agent.dimension_values.contracts import DimensionValuePlan, DimensionValueSpec, DimensionValueStatus


_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_CN_DATE = re.compile(r"(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日")


class GovernedDimensionValuePlanner:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = self._yaml("agent/contracts/dimension_value_policy.yml")
        self.semantic_policy = self._yaml("agent/contracts/semantic_query_policy.yml")
        metric_registry = self._yaml("metadata/datahub/governance/metric_registry.yml")
        self.governed_metrics = {item["id"] for item in metric_registry["metrics"]}
        self.governed_dimensions = set(self.semantic_policy["structured_filter_dimensions"])

    def plan(
        self,
        *,
        metrics: Iterable[str],
        dimension: str,
        question: str = "",
        limit: int | None = None,
    ) -> DimensionValuePlan:
        metric_names = tuple(dict.fromkeys(m.strip() for m in metrics if m.strip()))
        if not metric_names:
            return DimensionValuePlan(
                status=DimensionValueStatus.CLARIFICATION_REQUIRED,
                question=question,
                warnings=[
                    "Dimension-value discovery requires at least one governed metric context because "
                    "available dimensions/values are metric-path dependent."
                ],
            )

        max_metrics = int(self.policy["limits"]["max_metrics"])
        if len(metric_names) > max_metrics:
            return DimensionValuePlan(
                status=DimensionValueStatus.BLOCKED,
                question=question,
                warnings=[f"Dimension-value discovery supports at most {max_metrics} governed metrics."],
            )

        unknown_metrics = [name for name in metric_names if name not in self.governed_metrics]
        if unknown_metrics:
            return DimensionValuePlan(
                status=DimensionValueStatus.BLOCKED,
                question=question,
                warnings=["Ungoverned metric(s) are not eligible for dimension discovery: " + ", ".join(unknown_metrics)],
            )

        dimension = dimension.strip()
        if dimension not in self.governed_dimensions:
            return DimensionValuePlan(
                status=DimensionValueStatus.BLOCKED,
                question=question,
                warnings=[f"Dimension is outside the governed filter/discovery allowlist: {dimension}"],
            )

        dates = self._extract_dates(question)
        if len(dates) > 2:
            return DimensionValuePlan(
                status=DimensionValueStatus.CLARIFICATION_REQUIRED,
                question=question,
                warnings=["More than two calendar dates were found; provide at most one start/end range."],
            )

        start_time = end_time = None
        if dates:
            start_day = dates[0]
            end_day = dates[-1]
            if end_day < start_day:
                return DimensionValuePlan(
                    status=DimensionValueStatus.BLOCKED,
                    question=question,
                    warnings=["Dimension-value discovery end date is earlier than the start date."],
                )
            span_days = (end_day - start_day).days + 1
            max_days = int(self.policy["limits"]["max_time_range_days"])
            if span_days > max_days:
                return DimensionValuePlan(
                    status=DimensionValueStatus.BLOCKED,
                    question=question,
                    warnings=[f"Dimension-value discovery range is {span_days} days; contract maximum is {max_days}."],
                )
            start_time = f"{start_day.isoformat()}T00:00:00Z"
            end_time = f"{end_day.isoformat()}T23:59:59Z"

        value_limit = int(limit or self.policy["limits"]["default_values"])
        max_values = int(self.policy["limits"]["max_values"])
        if value_limit < 1 or value_limit > max_values:
            return DimensionValuePlan(
                status=DimensionValueStatus.BLOCKED,
                question=question,
                warnings=[f"Dimension-value limit must be between 1 and {max_values}."],
            )

        spec = DimensionValueSpec(
            metrics=metric_names,
            dimension=dimension,
            start_time=start_time,
            end_time=end_time,
            limit=value_limit,
        )
        return DimensionValuePlan(
            status=DimensionValueStatus.READY,
            question=question,
            spec=spec,
            command_preview=self.command_args(spec),
        )

    @staticmethod
    def command_args(spec: DimensionValueSpec) -> list[str]:
        args = [
            "mf",
            "list",
            "dimension-values",
            "--metrics",
            ",".join(spec.metrics),
            "--dimension",
            spec.dimension,
        ]
        if spec.start_time:
            args.extend(["--start-time", spec.start_time])
        if spec.end_time:
            args.extend(["--end-time", spec.end_time])
        return args

    def static_seed_values(self, dimension: str) -> list[str]:
        spec = self.semantic_policy["structured_filter_dimensions"][dimension]["value_source"]
        if spec.get("type") != "csv":
            return []
        path = self.root / spec["path"]
        column = spec["column"]
        if not path.exists():
            return []
        values: list[str] = []
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                value = str(row.get(column) or "").strip()
                if value and value not in values:
                    values.append(value)
        return values

    @staticmethod
    def _extract_dates(question: str) -> list[date]:
        found: list[tuple[int, date]] = []
        for regex in (_ISO_DATE, _CN_DATE):
            for match in regex.finditer(question):
                try:
                    found.append((match.start(), date(int(match.group(1)), int(match.group(2)), int(match.group(3)))))
                except ValueError:
                    continue
        return [item[1] for item in sorted(found, key=lambda x: x[0])]

    def _yaml(self, rel: str):
        return yaml.safe_load((self.root / rel).read_text(encoding="utf-8"))
