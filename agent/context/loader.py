"""Governed Context Loader（受治理上下文加载器）。

主流程：
    Router
      -> Context Planner
      -> Context Loader
      -> Planner
      -> Executor

Loader 不做第二次 Intent Classification，也不执行业务查询。

为了避免重复成本：
- Semantic / Metadata / Skill：规划阶段可物化；
- Runtime / Knowledge：只绑定后续 Tool handle，标记 EXECUTOR_OWNED；
- Code：若只是 optional，初始阶段不加载；
- Memory：当前未实现，Fail Closed。

V2 Observability：
- 只记录固定子阶段名称 + duration_ms；
- 不记录 Metric ID / Dataset / Prompt / Payload；
- 同名子阶段在一次 Load 内聚合，避免多 Metric 时把一次请求拆成多条样本；
- Timing 随 ContextBundle 内部返回，避免在共享 Runtime 上保存 mutable last_timing。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any, Callable

import yaml

from agent.code_context import GovernedModelContextRepository
from agent.skills import GovernedSkillRegistry, SkillResolutionStatus

from .budget import GovernedContextBudget
from .contracts import ContextPlan, ContextRequirement, ContextSource
from .cached_repository import GovernedContextRepository
from .runtime_contracts import (
    ContextBundle,
    ContextBundleStatus,
    ContextItem,
    ContextItemStatus,
)


class GovernedContextLoader:
    """把 ContextPlan 物化成最小规划上下文。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/context_loader_policy.yml").read_text(encoding="utf-8")
        )
        self.repo = GovernedContextRepository(self.root)

        # 性能边界：Git/dbt/MetricFlow 静态 Semantic Contract 在 Readiness 前预热。
        # API 的 /health/ready 会构造 Runtime，因此请求到达前 Snapshot 已经完成。
        # 如果 Snapshot 构建失败，Runtime 构造失败，Readiness 会 Fail Closed。
        self.repo.warm_semantic_snapshot()

        self.skills = GovernedSkillRegistry(self.root)
        self.code = GovernedModelContextRepository(self.root)
        self.budget = GovernedContextBudget(self.root)

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    @staticmethod
    def _measure(
        timings: list[tuple[str, float]],
        name: str,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """执行一个 Loader 子操作并记录耗时。

        `name` 必须是代码中固定的 bounded label，不能拼接用户输入、Metric ID 或路径。
        """

        started = perf_counter()
        value = operation(*args, **kwargs)
        timings.append(
            (
                name,
                max(0.0, (perf_counter() - started) * 1000),
            )
        )
        return value

    @staticmethod
    def _compact_timings(
        timings: list[tuple[str, float]],
    ) -> tuple[tuple[str, float], ...]:
        """把一次 Load 内的同名子阶段求和，形成每请求唯一 Timing Label。"""

        totals: dict[str, float] = {}
        order: list[str] = []
        for name, duration_ms in timings:
            if name not in totals:
                totals[name] = 0.0
                order.append(name)
            totals[name] += max(0.0, float(duration_ms))
        return tuple(
            (name, totals[name])
            for name in order
        )

    def load(self, route: Any, context_plan: ContextPlan) -> ContextBundle:
        """物化 required planning Context，并登记 optional / executor-owned Context。"""

        timings: list[tuple[str, float]] = []

        mismatch = self._measure(
            timings,
            "route_binding",
            self._validate_route_binding,
            route,
            context_plan,
        )
        if mismatch:
            return ContextBundle(
                context_plan=context_plan,
                status=ContextBundleStatus.BLOCKED,
                warnings=(mismatch,),
                substage_timings=self._compact_timings(timings),
            )

        items: list[ContextItem] = []
        for requirement in context_plan.requirements:
            if not requirement.required:
                items.append(
                    ContextItem(
                        source=requirement.source,
                        key=f"optional:{requirement.source.value}",
                        required=False,
                        status=ContextItemStatus.NOT_LOADED,
                        authority="CONTEXT_PLAN",
                        evidence_mode="OPTIONAL_NOT_MATERIALIZED",
                        warnings=(
                            "Optional Context is not loaded until a governed expansion reason is supplied.",
                        ),
                    )
                )
                continue

            if requirement.source is ContextSource.SEMANTIC:
                items.extend(
                    self._load_semantic(
                        route,
                        requirement,
                        timings,
                    )
                )
            elif requirement.source is ContextSource.METADATA:
                items.extend(
                    self._load_metadata(
                        route,
                        requirement,
                        timings,
                    )
                )
            elif requirement.source is ContextSource.SKILL:
                items.append(
                    self._load_skill(
                        route,
                        requirement,
                        timings,
                    )
                )
            elif requirement.source in {ContextSource.RUNTIME, ContextSource.KNOWLEDGE}:
                items.append(
                    self._measure(
                        timings,
                        "executor_owned_binding",
                        self._executor_owned,
                        route,
                        requirement,
                    )
                )
            elif requirement.source is ContextSource.CODE:
                # 当前策略不把 Code 配成 required；如果未来有人改 policy，
                # 这里仍 Fail Closed，避免默认把代码塞入 Runtime Context。
                items.append(
                    ContextItem(
                        source=ContextSource.CODE,
                        key="code:required_not_allowed",
                        required=True,
                        status=ContextItemStatus.BLOCKED,
                        authority="CODE_CONTEXT_POLICY",
                        warnings=(
                            "Code Context cannot be required in initial load; use progressive expansion.",
                        ),
                    )
                )
            elif requirement.source is ContextSource.MEMORY:
                items.append(
                    ContextItem(
                        source=ContextSource.MEMORY,
                        key="memory:not_implemented",
                        required=True,
                        status=ContextItemStatus.BLOCKED,
                        authority="MEMORY_POLICY",
                        warnings=("Governed long-term memory is not implemented yet.",),
                    )
                )

        bundle = self._measure(
            timings,
            "finalize",
            self._finalize,
            context_plan,
            tuple(items),
        )
        return replace(
            bundle,
            substage_timings=self._compact_timings(timings),
        )

    def _validate_route_binding(self, route: Any, plan: ContextPlan) -> str | None:
        intent = self._enum_value(getattr(route, "intent", "UNKNOWN"))
        if intent != plan.route_intent:
            return (
                f"Context Plan intent={plan.route_intent} does not match Router intent={intent}."
            )
        route_kind = getattr(route, "target_kind", None)
        route_id = getattr(route, "target_id", None)
        if route_kind != plan.target_kind or route_id != plan.target_id:
            return "Context Plan target does not match Router target."
        return None

    def _load_semantic(
        self,
        route: Any,
        requirement: ContextRequirement,
        timings: list[tuple[str, float]],
    ) -> list[ContextItem]:
        metrics = self._measure(
            timings,
            "semantic.target_resolution",
            self._metric_targets,
            route,
        )
        if not metrics:
            return [
                ContextItem(
                    source=ContextSource.SEMANTIC,
                    key="semantic:no_metric_target",
                    required=True,
                    status=ContextItemStatus.BLOCKED,
                    authority="dbt_metricflow",
                    warnings=(
                        "Required Semantic Context has no governed metric target to load.",
                    ),
                )
            ]

        items: list[ContextItem] = []
        for metric in metrics[: requirement.max_items]:
            payload = self._measure(
                timings,
                "semantic.repository_lookup",
                self.repo.metric_context,
                metric,
            )
            if payload is None:
                items.append(
                    ContextItem(
                        source=ContextSource.SEMANTIC,
                        key=f"metric:{metric}",
                        required=True,
                        status=ContextItemStatus.BLOCKED,
                        authority="dbt_metricflow",
                        warnings=(f"Governed metric context was not found: {metric}.",),
                    )
                )
                continue

            estimated_tokens = self._measure(
                timings,
                "semantic.token_estimate",
                self.budget.estimate,
                payload,
            )
            items.append(
                ContextItem(
                    source=ContextSource.SEMANTIC,
                    key=f"metric:{metric}",
                    required=True,
                    status=ContextItemStatus.LOADED,
                    payload=payload,
                    authority="dbt_metricflow",
                    evidence_mode="STATIC_GOVERNED_SEMANTIC",
                    estimated_tokens=estimated_tokens,
                )
            )
        return items

    def _load_metadata(
        self,
        route: Any,
        requirement: ContextRequirement,
        timings: list[tuple[str, float]],
    ) -> list[ContextItem]:
        kind = str(getattr(route, "target_kind", "") or "")
        target = str(getattr(route, "target_id", "") or "")
        intent = self._enum_value(getattr(route, "intent", ""))

        # Discovery 本身属于 bounded tool execution，避免 Loader 再实现一套搜索排序。
        if intent in {"METADATA_DISCOVERY", "METADATA_SEARCH"} or not target:
            return [
                ContextItem(
                    source=ContextSource.METADATA,
                    key="tool:search_metadata",
                    required=True,
                    status=ContextItemStatus.EXECUTOR_OWNED,
                    payload={
                        "tool": "search_metadata",
                        "arguments": {
                            "query": str(getattr(route, "question", "") or ""),
                            "limit": requirement.max_items,
                        },
                    },
                    authority="governed_metadata_tool",
                    evidence_mode="TOOL_BOUND",
                )
            ]

        if kind == "dataset":
            dataset = self._measure(
                timings,
                "metadata.repository_lookup",
                self.repo.dataset_context,
                target,
            )
            if dataset is None:
                return [self._metadata_missing(target, required=True)]

            payload: dict[str, Any] = {"dataset": dataset}
            if intent == "LINEAGE_QUERY":
                direction = (
                    "downstream"
                    if "downstream" in str(getattr(route, "question", "")).casefold()
                    or "下游" in str(getattr(route, "question", ""))
                    else "upstream"
                )
                payload["lineage"] = self._measure(
                    timings,
                    "metadata.lineage_lookup",
                    self.repo.static_lineage,
                    target,
                    direction=direction,
                    max_hops=min(2, requirement.max_items),
                )

            estimated_tokens = self._measure(
                timings,
                "metadata.token_estimate",
                self.budget.estimate,
                payload,
            )
            return [
                ContextItem(
                    source=ContextSource.METADATA,
                    key=f"dataset:{target}",
                    required=True,
                    status=ContextItemStatus.LOADED,
                    payload=payload,
                    authority="governed_metadata",
                    evidence_mode="STATIC_CONTRACT",
                    estimated_tokens=estimated_tokens,
                )
            ]

        if kind == "entity":
            entity = self._measure(
                timings,
                "metadata.repository_lookup",
                self.repo.entity_context,
                target,
            )
            if entity is None:
                return [self._metadata_missing(target, required=True)]

            estimated_tokens = self._measure(
                timings,
                "metadata.token_estimate",
                self.budget.estimate,
                entity,
            )
            return [
                ContextItem(
                    source=ContextSource.METADATA,
                    key=f"entity:{target}",
                    required=True,
                    status=ContextItemStatus.LOADED,
                    payload=entity,
                    authority="dbt_metricflow+governed_metadata",
                    evidence_mode="STATIC_CONTRACT",
                    estimated_tokens=estimated_tokens,
                )
            ]

        return [
            ContextItem(
                source=ContextSource.METADATA,
                key=f"metadata:unsupported_target:{kind}:{target}",
                required=True,
                status=ContextItemStatus.BLOCKED,
                authority="governed_metadata",
                warnings=(f"Metadata loader does not support target_kind={kind!r}.",),
            )
        ]

    def _load_skill(
        self,
        route: Any,
        requirement: ContextRequirement,
        timings: list[tuple[str, float]],
    ) -> ContextItem:
        resolution = self._measure(
            timings,
            "skill.resolve",
            self.skills.resolve,
            route,
        )
        if (
            resolution.status is not SkillResolutionStatus.RESOLVED
            or resolution.skill is None
        ):
            return ContextItem(
                source=ContextSource.SKILL,
                key="skill:unresolved",
                required=True,
                status=ContextItemStatus.BLOCKED,
                authority="governed_skill_registry",
                warnings=tuple(resolution.warnings)
                or ("No unique ACTIVE Analytics Skill was resolved.",),
            )

        skill = resolution.skill
        payload = {
            "id": skill.skill_id,
            "version": skill.version,
            "domain": skill.domain,
            "description": skill.description,
            "match": {
                "intents": list(skill.intents),
                "metrics": list(skill.metrics),
                "direction": skill.direction,
            },
            "requirements": {
                "required_metrics": list(skill.required_metrics),
                "optional_metrics": list(skill.optional_metrics),
                "dimensions": list(skill.dimensions),
            },
            "analysis_steps": [dict(step) for step in skill.analysis_steps],
            "guardrails": dict(skill.guardrails),
            "authority": dict(skill.authority),
            "source_path": skill.source_path,
        }
        estimated_tokens = self._measure(
            timings,
            "skill.token_estimate",
            self.budget.estimate,
            payload,
        )
        return ContextItem(
            source=ContextSource.SKILL,
            key=f"skill:{skill.skill_id}",
            required=True,
            status=ContextItemStatus.LOADED,
            payload=payload,
            authority="governed_skill_registry",
            evidence_mode="STATIC_SKILL_CONTRACT",
            estimated_tokens=estimated_tokens,
        )

    def _executor_owned(
        self,
        route: Any,
        requirement: ContextRequirement,
    ) -> ContextItem:
        """绑定后续工具，但不在 Context Loader 阶段重复执行。"""

        target = str(getattr(route, "target_id", "") or "")
        if requirement.source is ContextSource.RUNTIME:
            payload = {
                "tool": "get_runtime_context",
                "arguments": {"dataset": target},
            }
            key = f"tool:get_runtime_context:{target}"
            authority = "dagster_runtime_tool"
        else:
            # Knowledge 的 scope 已由 Router ToolPlan 决定；Loader 不重做 scope routing。
            payload = {
                "tool": "router_planned_knowledge_steps",
                "arguments": {"question": str(getattr(route, "question", "") or "")},
            }
            key = "tool:router_planned_knowledge_steps"
            authority = "governed_knowledge_tool"

        return ContextItem(
            source=requirement.source,
            key=key,
            required=True,
            status=ContextItemStatus.EXECUTOR_OWNED,
            payload=payload,
            authority=authority,
            evidence_mode="TOOL_BOUND",
            estimated_tokens=0,
            warnings=(
                "Context is intentionally deferred to the governed Executor to avoid duplicate runtime/retrieval cost.",
            ),
        )

    def _metric_targets(self, route: Any) -> tuple[str, ...]:
        """从 Router 已绑定目标 / ToolStep 中读取 Metric，不重新做 NLP。"""

        kind = str(getattr(route, "target_kind", "") or "")
        target = str(getattr(route, "target_id", "") or "")
        if kind in {"metric", "metric_set"} and target:
            return tuple(dict.fromkeys(x for x in target.split(",") if x))

        metrics: list[str] = []
        for step in getattr(route, "steps", ()) or ():
            arguments = getattr(step, "arguments", {}) or {}
            if arguments.get("metric"):
                metrics.append(str(arguments["metric"]))
            for metric in arguments.get("metrics", ()) or ():
                metrics.append(str(metric))
        return tuple(dict.fromkeys(metrics))

    @staticmethod
    def _metadata_missing(target: str, *, required: bool) -> ContextItem:
        return ContextItem(
            source=ContextSource.METADATA,
            key=f"metadata:{target}",
            required=required,
            status=ContextItemStatus.BLOCKED,
            authority="governed_metadata",
            warnings=(f"Governed metadata context was not found: {target}.",),
        )

    def _finalize(
        self,
        context_plan: ContextPlan,
        items: tuple[ContextItem, ...],
    ) -> ContextBundle:
        total = sum(item.estimated_tokens for item in items)
        required_failures = [
            item
            for item in items
            if item.required
            and item.status in {ContextItemStatus.BLOCKED, ContextItemStatus.ERROR}
        ]

        if required_failures:
            status = (
                ContextBundleStatus.ERROR
                if any(item.status is ContextItemStatus.ERROR for item in required_failures)
                else ContextBundleStatus.BLOCKED
            )
        elif total > self.budget.initial_limit:
            status = ContextBundleStatus.BLOCKED
            items = (
                *items,
                ContextItem(
                    source=ContextSource.SEMANTIC,
                    key="budget:initial",
                    required=True,
                    status=ContextItemStatus.BLOCKED,
                    authority="context_budget",
                    warnings=(
                        f"Initial Context estimate {total} exceeds governed limit {self.budget.initial_limit}.",
                    ),
                ),
            )
        else:
            status = ContextBundleStatus.READY

        return ContextBundle(
            context_plan=context_plan,
            items=tuple(items),
            status=status,
            estimated_tokens=total,
            warnings=tuple(context_plan.warnings),
        )
