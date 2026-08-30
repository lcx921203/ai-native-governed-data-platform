"""Fail-closed governed dimension-value resolver.

The resolver reuses Phase 5C as the value-universe provider.  It never turns arbitrary
user text into a MetricFlow predicate.  Only a unique exact/normalized/alias match may
become a resolved filter.  Fuzzy similarity is returned only as a clarification candidate.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import yaml

from agent.dimension_values.executor import MetricFlowDimensionValueExecutor
from agent.dimension_values.planner import GovernedDimensionValuePlanner
from agent.dimension_resolution.contracts import (
    DimensionResolutionMode,
    DimensionResolutionResult,
    DimensionResolutionStatus,
    DimensionValueCandidate,
)


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_QUOTE_CHARS = "\"'“”‘’`"


def normalize_value(value: str) -> str:
    value = value.strip().strip(_QUOTE_CHARS).lower().replace("_", " ").replace("-", " ")
    value = re.sub(r"[^\w\u3400-\u9fff]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


class GovernedDimensionValueResolver:
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = self._yaml("agent/contracts/dimension_resolution_policy.yml")
        self.semantic_policy = self._yaml("agent/contracts/semantic_query_policy.yml")
        registry = self._yaml("metadata/datahub/governance/metric_registry.yml")
        self.governed_metrics = {item["id"] for item in registry["metrics"]}
        self.governed_dimensions = tuple(self.semantic_policy["structured_filter_dimensions"].keys())
        self.discovery_planner = GovernedDimensionValuePlanner(self.root)
        self.discovery_executor = MetricFlowDimensionValueExecutor(self.root)

    def resolve(
        self,
        *,
        metrics: Iterable[str],
        raw_value: str,
        dimension_hint: str | None = None,
        question: str = "",
    ) -> DimensionResolutionResult:
        metric_names = tuple(dict.fromkeys(item.strip() for item in metrics if item.strip()))
        raw_value = self._clean_raw_value(raw_value)

        validation = self._validate(metric_names, raw_value, dimension_hint)
        if validation is not None:
            return validation

        dimensions = (dimension_hint,) if dimension_hint else self.governed_dimensions
        discovered: dict[str, tuple[list[str], str, str]] = {}
        warnings: list[str] = []
        for dimension in dimensions:
            values, evidence, source_mode, discovery_warnings = self._discover_values(
                metrics=metric_names,
                dimension=dimension,
                question=question,
            )
            discovered[dimension] = (values, evidence, source_mode)
            warnings.extend(discovery_warnings)
        warnings = list(dict.fromkeys(warnings))

        exact = self._exact_candidates(raw_value, discovered)
        if len(exact) == 1:
            item = exact[0]
            return DimensionResolutionResult(
                status=DimensionResolutionStatus.RESOLVED,
                raw_value=raw_value,
                metrics=metric_names,
                dimension_hint=dimension_hint,
                resolved_dimension=item.dimension,
                resolved_value=item.value,
                mode=item.mode,
                evidence=item.evidence,
                source_mode=item.source_mode,
                candidates=[item],
                warnings=warnings,
            )
        if len(exact) > 1:
            return DimensionResolutionResult(
                status=DimensionResolutionStatus.CLARIFICATION_REQUIRED,
                raw_value=raw_value,
                metrics=metric_names,
                dimension_hint=dimension_hint,
                mode=DimensionResolutionMode.NONE,
                evidence=self._combined_evidence(exact),
                source_mode="AMBIGUOUS_EXACT",
                candidates=exact[: self._max_candidates],
                warnings=[
                    f"Filter value {raw_value!r} matched more than one governed dimension/value; "
                    "an explicit dimension is required before querying."
                ] + warnings,
            )

        fuzzy = self._fuzzy_candidates(raw_value, discovered)
        if fuzzy:
            return DimensionResolutionResult(
                status=DimensionResolutionStatus.CLARIFICATION_REQUIRED,
                raw_value=raw_value,
                metrics=metric_names,
                dimension_hint=dimension_hint,
                mode=DimensionResolutionMode.FUZZY_CANDIDATE,
                evidence=self._combined_evidence(fuzzy),
                source_mode="CANDIDATE_ONLY",
                candidates=fuzzy[: self._max_candidates],
                warnings=[
                    "Only fuzzy candidates were found. Phase 5D never auto-applies a fuzzy value; "
                    "the user must confirm the intended canonical value."
                ] + warnings,
            )

        return DimensionResolutionResult(
            status=DimensionResolutionStatus.NOT_FOUND,
            raw_value=raw_value,
            metrics=metric_names,
            dimension_hint=dimension_hint,
            warnings=[
                f"No governed dimension value matched {raw_value!r}. Do not drop the requested filter; "
                "refresh runtime dimension values or ask the user to clarify."
            ] + warnings,
        )

    def extract_raw_value(self, question: str, dimension_hint: str | None = None) -> str | None:
        """Conservatively extract one filter literal from supported NL shapes.

        Examples:
        - ``品牌为 Coca Cola`` -> ``Coca Cola``
        - ``只看 South 的 gross_sales`` -> ``South``

        This is intentionally not a general NLU parser. If the shape is ambiguous we return
        ``None`` so the caller can ask for clarification instead of silently querying without
        the requested filter.
        """
        text = question.strip()
        if not text:
            return None

        # Prefer an explicit governed dimension mention + assignment marker.
        if dimension_hint:
            config = self.semantic_policy["structured_filter_dimensions"].get(dimension_hint, {})
            aliases = sorted(config.get("dimension_aliases", []), key=len, reverse=True)
            assignments = self.policy["extraction"].get("assignment_markers", [])
            for alias in aliases:
                for marker in assignments:
                    pattern = re.compile(
                        rf"{re.escape(alias)}\s*{re.escape(marker)}\s*[\"'“”‘’]?([^，,。；;\n]+)",
                        re.IGNORECASE,
                    )
                    match = pattern.search(text)
                    if match:
                        return self._trim_extracted_value(match.group(1))

        markers = sorted(self.policy["extraction"].get("explicit_filter_markers", []), key=len, reverse=True)
        for marker in markers:
            if _CJK_RE.search(marker):
                pattern = re.compile(rf"{re.escape(marker)}\s*[\"'“”‘’]?([^，,。；;\n]+)", re.IGNORECASE)
            else:
                pattern = re.compile(rf"(?<!\w){re.escape(marker)}\s+[\"']?([^,.;\n]+)", re.IGNORECASE)
            match = pattern.search(text)
            if match:
                return self._trim_extracted_value(match.group(1))
        return None

    def _trim_extracted_value(self, value: str) -> str | None:
        value = value.strip().strip(_QUOTE_CHARS).strip()
        # Common Chinese possessive connector: ``只看 South 的 gross_sales``.
        value = re.split(r"\s+的\s*|的(?=[A-Za-z_])", value, maxsplit=1)[0].strip()
        value = re.sub(r"的$", "", value).strip()
        # Stop before a governed metric alias/id if it follows the literal.
        lowered = value.lower()
        for metric in sorted(self.governed_metrics, key=len, reverse=True):
            for needle in (metric, metric.replace("_", " ")):
                pos = lowered.find(needle.lower())
                if pos > 0:
                    value = value[:pos].strip()
                    lowered = value.lower()
        # Remove trailing generic query words.
        value = re.sub(r"\s+(是多少|多少|趋势|按天|按周|按月|how much|trend)\s*$", "", value, flags=re.IGNORECASE)
        value = value.strip().strip(_QUOTE_CHARS).strip()
        return value or None

    def _validate(
        self,
        metric_names: tuple[str, ...],
        raw_value: str,
        dimension_hint: str | None,
    ) -> DimensionResolutionResult | None:
        if not metric_names:
            return self._blocked(raw_value, metric_names, dimension_hint, "Dimension resolution requires metric context.")
        max_metrics = int(self.policy["limits"]["max_metrics"])
        if len(metric_names) > max_metrics:
            return self._blocked(raw_value, metric_names, dimension_hint, f"At most {max_metrics} metrics may share one value-resolution context.")
        unknown = [metric for metric in metric_names if metric not in self.governed_metrics]
        if unknown:
            return self._blocked(raw_value, metric_names, dimension_hint, "Ungoverned metric(s): " + ", ".join(unknown))
        if not raw_value:
            return self._blocked(raw_value, metric_names, dimension_hint, "A non-empty filter literal is required.")
        if len(raw_value) > int(self.policy["limits"]["max_raw_value_length"]):
            return self._blocked(raw_value, metric_names, dimension_hint, "Filter literal exceeds the governed maximum length.")
        if dimension_hint and dimension_hint not in self.governed_dimensions:
            return self._blocked(raw_value, metric_names, dimension_hint, f"Ungoverned dimension hint: {dimension_hint}")
        lowered = f" {raw_value.lower()} "
        for marker in self.policy.get("prohibited_value_markers", []):
            if marker.lower() in lowered or marker in raw_value:
                return self._blocked(raw_value, metric_names, dimension_hint, f"Raw predicate/query syntax is prohibited in filter values ({marker!r}).")
        return None

    def _discover_values(
        self,
        *,
        metrics: tuple[str, ...],
        dimension: str,
        question: str,
    ) -> tuple[list[str], str, str, list[str]]:
        plan = self.discovery_planner.plan(
            metrics=metrics,
            dimension=dimension,
            question=question,
            limit=int(self.policy["limits"]["max_discovery_values_per_dimension"]),
        )
        result = self.discovery_executor.execute(plan)
        if result.status.value in {"BLOCKED", "ERROR", "CLARIFICATION_REQUIRED"}:
            return [], result.evidence, result.source_mode, list(result.warnings)
        return list(result.values), result.evidence, result.source_mode, list(result.warnings)

    def _exact_candidates(
        self,
        raw_value: str,
        discovered: dict[str, tuple[list[str], str, str]],
    ) -> list[DimensionValueCandidate]:
        raw_norm = normalize_value(raw_value)
        candidates: list[DimensionValueCandidate] = []
        for dimension, (values, evidence, source_mode) in discovered.items():
            aliases_by_value = self.semantic_policy["structured_filter_dimensions"].get(dimension, {}).get("value_aliases", {})
            for canonical in values:
                if raw_value == canonical:
                    mode = DimensionResolutionMode.CANONICAL_EXACT
                elif raw_norm == normalize_value(canonical):
                    mode = DimensionResolutionMode.NORMALIZED_EXACT
                else:
                    aliases = aliases_by_value.get(canonical, [])
                    alias_norms = {normalize_value(alias) for alias in aliases}
                    if raw_norm not in alias_norms:
                        continue
                    mode = DimensionResolutionMode.ALIAS_EXACT
                candidates.append(
                    DimensionValueCandidate(
                        dimension=dimension,
                        value=canonical,
                        score=1.0,
                        mode=mode,
                        evidence=evidence,
                        source_mode=source_mode,
                    )
                )
        return self._dedupe_candidates(candidates)

    def _fuzzy_candidates(
        self,
        raw_value: str,
        discovered: dict[str, tuple[list[str], str, str]],
    ) -> list[DimensionValueCandidate]:
        raw_norm = normalize_value(raw_value)
        if len(raw_norm) < 3:
            return []
        threshold = float(self.policy["limits"]["fuzzy_candidate_threshold"])
        ranked: list[DimensionValueCandidate] = []
        for dimension, (values, evidence, source_mode) in discovered.items():
            for canonical in values:
                score = SequenceMatcher(a=raw_norm, b=normalize_value(canonical)).ratio()
                if score < threshold:
                    continue
                ranked.append(
                    DimensionValueCandidate(
                        dimension=dimension,
                        value=canonical,
                        score=round(score, 4),
                        mode=DimensionResolutionMode.FUZZY_CANDIDATE,
                        evidence=evidence,
                        source_mode=source_mode,
                    )
                )
        ranked = self._dedupe_candidates(ranked)
        ranked.sort(key=lambda item: (-item.score, item.dimension, item.value))
        return ranked[: self._max_candidates]

    @staticmethod
    def _dedupe_candidates(items: list[DimensionValueCandidate]) -> list[DimensionValueCandidate]:
        result: list[DimensionValueCandidate] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = (item.dimension, item.value)
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    @property
    def _max_candidates(self) -> int:
        return int(self.policy["limits"]["max_candidates"])

    @staticmethod
    def _combined_evidence(items: list[DimensionValueCandidate]) -> str:
        return "RUNTIME_VERIFIED" if items and all(item.evidence == "RUNTIME_VERIFIED" for item in items) else "STATIC_CONTRACT"

    @staticmethod
    def _clean_raw_value(value: str) -> str:
        return value.strip().strip(_QUOTE_CHARS).strip()

    def _blocked(
        self,
        raw_value: str,
        metrics: tuple[str, ...],
        dimension_hint: str | None,
        warning: str,
    ) -> DimensionResolutionResult:
        return DimensionResolutionResult(
            status=DimensionResolutionStatus.BLOCKED,
            raw_value=raw_value,
            metrics=metrics,
            dimension_hint=dimension_hint,
            warnings=[warning],
        )

    def _yaml(self, relative: str) -> dict[str, Any]:
        return yaml.safe_load((self.root / relative).read_text(encoding="utf-8"))
