"""自然语言到受限 MetricFlow 查询计划的确定性规划器。

业务逻辑：只允许受治理 Metric、显式日期、有限 group-by 与结构化维度过滤。
MetricFlow API：这里只生成 explain/query 参数，不执行 SQL。
工程边界：拒绝 raw SQL / caller-supplied --where，信息不足时返回 CLARIFICATION_REQUIRED。
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import yaml

from agent.dimension_resolution import (
    DimensionResolutionStatus,
    GovernedDimensionValueResolver,
)
from agent.semantic_query.contracts import (
    SemanticDimensionFilter,
    SemanticFilterOperator,
    SemanticQueryClarification,
    SemanticQueryPlan,
    SemanticQuerySpec,
    SemanticQueryStatus,
)


_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_CN_DATE = re.compile(r"(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _norm(value: str) -> str:
    """把别名文本做大小写、下划线与连字符归一化，供确定性匹配使用。"""
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


def _contains(text: str, phrase: str) -> bool:
    """判断一个受治理短语是否以安全边界出现在问题中。"""
    return _match_position(text, phrase) is not None


def _match_position(text: str, phrase: str) -> int | None:
    """返回受治理短语在问题中的稳定位置。
    
    对短 Latin 值使用字母数字边界，避免 CA 误匹配 Coca；这里是在保护维度值解析，不做模糊猜测。
    """
    phrase = phrase.strip()
    if not phrase:
        return None
    if _CJK_RE.search(phrase):
        pos = text.lower().find(phrase.lower())
        return pos if pos >= 0 else None

    # For Latin aliases, allow spaces / punctuation literally and require an alphanumeric
    # boundary so `US` cannot match inside an unrelated word.
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )
    match = pattern.search(text)
    if match:
        return match.start()

    # For explicit ids containing separators, allow users to type spaces/hyphens while
    # preserving word boundaries. Never use a raw normalized substring fallback: a short
    # governed value such as ``CA`` must not match inside ``Coca``.
    if "_" in phrase or "-" in phrase:
        parts = [part for part in re.split(r"[_\s-]+", phrase) if part]
        if parts:
            flexible = r"[\s_-]+".join(re.escape(part) for part in parts)
            match = re.search(rf"(?<![A-Za-z0-9]){flexible}(?![A-Za-z0-9])", text, re.IGNORECASE)
            if match:
                return match.start()
    return None


class GovernedSemanticQueryPlanner:
    """把受治理 Metric 与自然语言约束转换成 SemanticQueryPlan。
    
    输入包括 metric(s)、question、limit；输出是 READY / CLARIFICATION_REQUIRED / BLOCKED。
    工程边界：公式权威仍属于 dbt + MetricFlow，本类不生成任意 SQL。
    """
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = self._yaml("agent/contracts/semantic_query_policy.yml")
        registry = self._yaml("metadata/datahub/governance/metric_registry.yml")
        self.governed_metrics = {item["id"] for item in registry["metrics"]}
        self._dimension_values = self._load_governed_dimension_values()
        self.value_resolver = GovernedDimensionValueResolver(self.root)

    def plan(self, *, metric: str, question: str, limit: int | None = None) -> SemanticQueryPlan:
        """单 Metric 的兼容入口，统一转交 plan_metrics，避免维护两套规划逻辑。"""
        return self.plan_metrics(metrics=[metric], question=question, limit=limit)

    def plan_metrics(
        self,
        *,
        metrics: Iterable[str],
        question: str,
        limit: int | None = None,
    ) -> SemanticQueryPlan:
        """构建完整受治理语义查询计划。
        
        依次校验 Metric 数量/治理状态、禁止自由谓词、日期范围、group-by、行数与维度值解析。
        输入不足时返回 Clarification，不会用默认日期或近似维度替用户做决定。
        """
        metric_names = tuple(dict.fromkeys(item.strip() for item in metrics if item.strip()))
        if not metric_names:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question=question,
                warnings=["At least one governed metric is required."],
            )

        max_metrics = int(self.policy["limits"]["max_metrics"])
        if len(metric_names) > max_metrics:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=[f"Semantic query requested {len(metric_names)} metrics; contract maximum is {max_metrics}."],
            )

        ungoverned = [metric for metric in metric_names if metric not in self.governed_metrics]
        if ungoverned:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=["Ungoverned metric(s) are not queryable: " + ", ".join(ungoverned)],
            )

        prohibited = self._prohibited_free_form_filter_marker(question)
        if prohibited:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=[
                    f"Raw/free-form predicate syntax is not accepted ({prohibited!r}). "
                    "Use a governed natural-language dimension filter instead."
                ],
            )

        dates = self._extract_dates(question)
        if not dates:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question=question,
                warnings=["An explicit calendar date or date range is required before querying business data."],
            )
        if len(dates) > 2:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question=question,
                warnings=["More than two calendar dates were found; provide one date or one start/end range."],
            )

        start_day = dates[0]
        end_day = dates[-1]
        if end_day < start_day:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=["Semantic query end date is earlier than the start date."],
            )
        span_days = (end_day - start_day).days + 1
        max_days = int(self.policy["limits"]["max_time_range_days"])
        if span_days > max_days:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=[f"Semantic query range is {span_days} days; contract maximum is {max_days}."],
            )

        group_by = self._group_bys(question)
        max_group = int(self.policy["limits"]["max_group_by"])
        if len(group_by) > max_group:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=[f"Semantic query requested {len(group_by)} group-bys; contract maximum is {max_group}."],
            )
        if self._contains_any(question, self.policy.get("trend_markers", [])) and not any(
            value.startswith("metric_time__") for value in group_by
        ):
            return SemanticQueryPlan(
                status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question=question,
                warnings=["A trend query requires an explicit time grain: day, week, or month."],
            )

        row_limit = int(limit or self.policy["limits"]["default_rows"])
        max_rows = int(self.policy["limits"]["max_rows"])
        if row_limit < 1 or row_limit > max_rows:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=[f"Semantic query row limit must be between 1 and {max_rows}."],
            )

        filters, filter_warning = self._structured_filters(question, group_by)
        if filter_warning:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                question=question,
                warnings=[filter_warning],
            )

        base_spec = SemanticQuerySpec(
            metric=metric_names[0],
            metrics=metric_names,
            start_time=f"{start_day.isoformat()}T00:00:00Z",
            end_time=f"{end_day.isoformat()}T23:59:59Z",
            group_by=tuple(group_by),
            filters=tuple(filters),
            limit=row_limit,
        )

        resolution = self._dynamic_filter_resolution(metric_names, question)
        if resolution is not None:
            if resolution.status is DimensionResolutionStatus.RESOLVED:
                assert resolution.resolved_dimension and resolution.resolved_value is not None
                existing = [f for f in filters if f.dimension == resolution.resolved_dimension]
                if existing and any(f.value != resolution.resolved_value for f in existing):
                    return SemanticQueryPlan(
                        status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                        question=question,
                        continuation_spec=base_spec,
                        warnings=[
                            f"Conflicting values were resolved for {resolution.resolved_dimension}; clarify the intended filter value."
                        ],
                    )
                if not existing:
                    filters.append(
                        SemanticDimensionFilter(
                            dimension=resolution.resolved_dimension,
                            operator=SemanticFilterOperator.EQ,
                            value=resolution.resolved_value,
                            source=f"dimension_value_resolution:{resolution.mode.value}:{resolution.evidence}",
                        )
                    )
            elif resolution.status in {DimensionResolutionStatus.CLARIFICATION_REQUIRED, DimensionResolutionStatus.NOT_FOUND}:
                candidates = tuple(candidate.to_dict() for candidate in resolution.candidates)
                if candidates:
                    candidate_text = ", ".join(f"{c['dimension']}={c['value']}" for c in candidates)
                    first_warning = f"Filter value {resolution.raw_value!r} requires confirmation. Candidate(s): {candidate_text}."
                    prompt = f"请确认筛选值 {resolution.raw_value!r}：" + "；".join(
                        f"{index}. {c['dimension']}={c['value']}" for index, c in enumerate(candidates, 1)
                    )
                    kind = "DIMENSION_VALUE_CONFIRMATION"
                else:
                    first_warning = f"Filter value {resolution.raw_value!r} could not be resolved to a governed dimension value; the filter was not dropped."
                    prompt = f"请明确筛选值 {resolution.raw_value!r} 对应的受治理维度/值。"
                    kind = "DIMENSION_VALUE_CLARIFICATION"
                clarification = SemanticQueryClarification(
                    kind=kind,
                    raw_value=resolution.raw_value,
                    dimension_hint=resolution.dimension_hint,
                    candidates=candidates,
                    evidence=resolution.evidence,
                    source_mode=resolution.source_mode,
                    prompt=prompt,
                )
                return SemanticQueryPlan(
                    status=SemanticQueryStatus.CLARIFICATION_REQUIRED,
                    question=question,
                    continuation_spec=base_spec,
                    clarification=clarification,
                    warnings=[first_warning, *resolution.warnings],
                )
            elif resolution.status is DimensionResolutionStatus.BLOCKED:
                return SemanticQueryPlan(
                    status=SemanticQueryStatus.BLOCKED,
                    question=question,
                    warnings=list(resolution.warnings),
                )
            elif resolution.status is DimensionResolutionStatus.ERROR:
                return SemanticQueryPlan(
                    status=SemanticQueryStatus.ERROR,
                    question=question,
                    warnings=list(resolution.warnings),
                )

        max_filters = int(self.policy["limits"]["max_filters"])
        if len(filters) > max_filters:
            return SemanticQueryPlan(
                status=SemanticQueryStatus.BLOCKED,
                question=question,
                warnings=[f"Semantic query requested {len(filters)} filters; contract maximum is {max_filters}."],
            )

        spec = SemanticQuerySpec(
            metric=metric_names[0],
            metrics=metric_names,
            start_time=base_spec.start_time,
            end_time=base_spec.end_time,
            group_by=base_spec.group_by,
            filters=tuple(filters),
            limit=row_limit,
        )
        return SemanticQueryPlan(
            status=SemanticQueryStatus.READY,
            question=question,
            spec=spec,
            command_preview=self.command_args(spec),
        )

    def _dynamic_filter_resolution(self, metric_names: tuple[str, ...], question: str):
        """对静态别名无法安全拥有的字面值调用受治理 Dimension Value Resolver；只接受可证明的解析结果。"""
        hint = self._dimension_assignment_hint(question)
        raw = None
        if hint:
            raw = self.value_resolver.extract_raw_value(question, hint)
        else:
            markers = self.value_resolver.policy.get("extraction", {}).get("explicit_filter_markers", [])
            if any(_contains(question, marker) for marker in markers):
                raw = self.value_resolver.extract_raw_value(question, None)
        if not raw:
            return None
        return self.value_resolver.resolve(
            metrics=metric_names,
            raw_value=raw,
            dimension_hint=hint,
            question=question,
        )

    def _dimension_assignment_hint(self, question: str) -> str | None:
        """从问题中识别“某值属于某维度”的显式语义提示，降低跨维度误绑定风险。"""
        hits: list[str] = []
        markers = self.value_resolver.policy.get("extraction", {}).get("assignment_markers", [])
        for dimension, config in self.policy.get("structured_filter_dimensions", {}).items():
            for alias in sorted(config.get("dimension_aliases", []), key=len, reverse=True):
                for marker in markers:
                    pattern = re.compile(rf"{re.escape(alias)}\s*{re.escape(marker)}", re.IGNORECASE)
                    if pattern.search(question):
                        hits.append(dimension)
                        break
                if dimension in hits:
                    break
        unique = list(dict.fromkeys(hits))
        return unique[0] if len(unique) == 1 else None

    def command_args(self, spec: SemanticQuerySpec) -> list[str]:
        """把 READY 的 SemanticQuerySpec 转成受限 MetricFlow query CLI 参数。"""
        args = ["mf", "query", "--metrics", ",".join(spec.metric_names)]
        if spec.group_by:
            args.extend(["--group-by", ",".join(spec.group_by)])
        for item in spec.filters:
            args.extend(["--where", self.filter_expression(item)])
        args.extend([
            "--start-time", spec.start_time,
            "--end-time", spec.end_time,
            "--limit", str(spec.limit),
        ])
        return args

    def explain_args(self, spec: SemanticQuerySpec) -> list[str]:
        """把 READY 的 SemanticQuerySpec 转成 MetricFlow explain 参数，用于执行前验证语义路径。"""
        return [*self.command_args(spec), "--explain", "--show-dataflow-plan"]

    @staticmethod
    def filter_expression(item: SemanticDimensionFilter) -> str:
        """只从结构化 SemanticDimensionFilter 生成 MetricFlow 过滤表达式；不接受调用方直接传 raw where。"""
        if item.operator is not SemanticFilterOperator.EQ:
            raise ValueError(f"Unsupported governed filter operator: {item.operator.value}")
        # Values originate only from repo-governed canonical value sources. Still escape
        # a single quote defensively because the result is a MetricFlow expression literal.
        value = item.value.replace("'", "''")
        return f"{{{{ Dimension('{item.dimension}') }}}} = '{value}'"

    def _extract_dates(self, question: str) -> list[date]:
        """提取 ISO / 中文显式日历日期并转换为 date；不在这里猜相对时间。"""
        found: list[tuple[int, date]] = []
        for regex in (_ISO_DATE, _CN_DATE):
            for match in regex.finditer(question):
                try:
                    found.append((match.start(), date(int(match.group(1)), int(match.group(2)), int(match.group(3)))))
                except ValueError:
                    continue
        ordered: list[date] = []
        seen: set[tuple[int, date]] = set()
        for position, value in sorted(found, key=lambda item: item[0]):
            key = (position, value)
            if key not in seen:
                seen.add(key)
                ordered.append(value)
        return ordered

    def _group_bys(self, question: str) -> list[str]:
        """根据受治理维度与时间粒度标记解析 group-by，保持数量上限。"""
        matches: list[tuple[int, str]] = []
        for dimension, aliases in self.policy.get("group_by_aliases", {}).items():
            positions = [pos for alias in aliases if (pos := _match_position(question, alias)) is not None]
            if positions:
                matches.append((min(positions), dimension))
        return [dimension for _, dimension in sorted(matches)]

    def _structured_filters(
        self,
        question: str,
        group_by: list[str],
    ) -> tuple[list[SemanticDimensionFilter], str | None]:
        """从治理配置与问题文本生成 equality filter；遇到冲突或歧义返回澄清提示。"""
        matched: list[tuple[int, SemanticDimensionFilter]] = []
        configs = self.policy.get("structured_filter_dimensions", {})

        for dimension, config in configs.items():
            value_hits: list[tuple[int, str]] = []
            canonical_values = self._dimension_values.get(dimension, set())
            aliases_by_value = config.get("value_aliases", {})
            for canonical in sorted(canonical_values):
                aliases = [canonical, *aliases_by_value.get(canonical, [])]
                positions = [pos for alias in aliases if (pos := _match_position(question, alias)) is not None]
                if positions:
                    value_hits.append((min(positions), canonical))

            distinct_values = list(dict.fromkeys(value for _, value in sorted(value_hits)))
            if len(distinct_values) > 1:
                return [], (
                    f"Multiple values were detected for governed dimension {dimension}: "
                    + ", ".join(distinct_values)
                    + ". Phase 5B supports one equality value per dimension."
                )
            if distinct_values:
                canonical = distinct_values[0]
                position = min(pos for pos, value in value_hits if value == canonical)
                matched.append(
                    (
                        position,
                        SemanticDimensionFilter(
                            dimension=dimension,
                            operator=SemanticFilterOperator.EQ,
                            value=canonical,
                        ),
                    )
                )
                continue

            # Explicit filter wording that names a governed dimension but provides no
            # governed value must not silently fall back to an unfiltered query.
            if dimension in group_by:
                continue
            dimension_mentioned = any(
                _contains(question, alias) for alias in config.get("dimension_aliases", [])
            )
            filter_intent = self._contains_any(question, self.policy.get("filter_intent_markers", []))
            if dimension_mentioned and filter_intent:
                return [], (
                    f"A filter on {dimension} was requested, but no governed canonical value was resolved. "
                    "Use a known governed value or refresh the dimension-value contract."
                )

        return [item for _, item in sorted(matched, key=lambda pair: (pair[0], pair[1].dimension))], None

    def _load_governed_dimension_values(self) -> dict[str, set[str]]:
        """读取仓库内治理维度值字典，作为静态可验证的过滤值来源。"""
        result: dict[str, set[str]] = {}
        for dimension, config in self.policy.get("structured_filter_dimensions", {}).items():
            source = config.get("value_source", {})
            if source.get("type") != "csv":
                raise ValueError(f"Unsupported Phase 5B value source for {dimension}: {source.get('type')}")
            path = self.root / source["path"]
            column = source["column"]
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if column not in (reader.fieldnames or []):
                    raise ValueError(f"Governed dimension source {path} does not contain column {column}")
                values = {str(row.get(column) or "").strip() for row in reader}
            values.discard("")
            result[dimension] = values

            aliases = config.get("value_aliases", {})
            unknown_alias_keys = sorted(set(aliases) - values)
            if unknown_alias_keys:
                raise ValueError(
                    f"Value aliases for {dimension} reference canonical value(s) absent from {path}: "
                    + ", ".join(unknown_alias_keys)
                )
        return result

    def _prohibited_free_form_filter_marker(self, question: str) -> str | None:
        """检测 SQL、raw --where 等越过语义契约的自由谓词标记。"""
        lowered = f" {question.lower()} "
        for marker in self.policy.get("prohibited_free_form_filter_markers", []):
            if marker.lower() in lowered:
                return marker
        # Caller-supplied equality syntax is also raw predicate syntax. Natural-language
        # “为/等于” remains allowed and is resolved via the governed value dictionary.
        if "=" in question:
            return "="
        return None

    @staticmethod
    def _contains_any(question: str, markers: list[str]) -> bool:
        """判断问题是否包含任一治理标记，供 trend / group-by 等有限规则复用。"""
        return any(_contains(question, marker) for marker in markers)

    def _yaml(self, relative: str) -> dict[str, Any]:
        """从项目根目录读取 YAML 治理契约；配置缺失应显式失败而不是静默使用默认语义。"""
        return yaml.safe_load((self.root / relative).read_text(encoding="utf-8"))
