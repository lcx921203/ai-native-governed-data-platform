"""Derive conservative contribution additivity from the canonical MetricFlow definitions."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class MetricContributionSemantics:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.definitions = self._load_definitions()
        self._memo: dict[str, bool] = {}

    def is_additive(self, metric: str) -> bool:
        if metric in self._memo:
            return self._memo[metric]
        definition = self.definitions.get(metric)
        if not definition:
            self._memo[metric] = False
            return False
        metric_type = str(definition.get("type") or "").lower()
        if metric_type == "simple":
            result = str(definition.get("agg") or "").lower() == "sum"
        elif metric_type == "derived":
            inputs = [
                item["name"] if isinstance(item, dict) else str(item)
                for item in definition.get("input_metrics", [])
            ]
            expr = str(definition.get("expr") or "")
            result = bool(inputs) and self._is_linear_plus_minus(expr, inputs) and all(
                self.is_additive(name) for name in inputs
            )
        else:
            # ratio, average, count_distinct and unknown types remain conservative.
            result = False
        self._memo[metric] = result
        return result

    def reason(self, metric: str) -> str:
        definition = self.definitions.get(metric) or {}
        metric_type = str(definition.get("type") or "unknown")
        agg = str(definition.get("agg") or "")
        if self.is_additive(metric):
            return f"{metric} is contribution-additive under canonical MetricFlow semantics ({metric_type}{'/' + agg if agg else ''})."
        return f"{metric} is not contribution-additive under the conservative MetricFlow-derived rule ({metric_type}{'/' + agg if agg else ''})."

    def _load_definitions(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        semantic = yaml.safe_load(
            (self.root / "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml").read_text(encoding="utf-8")
        )
        for model in semantic.get("models", []):
            for metric in model.get("metrics", []):
                result[metric["name"]] = dict(metric)
        metrics_dir = self.root / "dbt/mercaso_dbt/models/metrics"
        for path in sorted(metrics_dir.glob("*.yml")):
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for metric in payload.get("metrics", []):
                result[metric["name"]] = dict(metric)
        return result

    @staticmethod
    def _is_linear_plus_minus(expr: str, inputs: list[str]) -> bool:
        if not expr.strip():
            return False
        scrubbed = expr
        for name in sorted(inputs, key=len, reverse=True):
            scrubbed = re.sub(rf"\b{re.escape(name)}\b", "", scrubbed)
        # Only + / - / parentheses / whitespace are allowed after removing input names.
        return re.fullmatch(r"[\s+\-()]+", scrubbed) is not None
