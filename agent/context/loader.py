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
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agent.code_context import GovernedModelContextRepository
from agent.skills import GovernedSkillRegistry, SkillResolutionStatus

from .budget import GovernedContextBudget
from .contracts import ContextPlan, ContextRequirement, ContextSource
from .repository import GovernedContextRepository
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
        self.skills = GovernedSkillRegistry(self.root)
        self.code = GovernedModelContextRepository(self.root)
        self.budget = GovernedContextBudget(self.root)

    @staticmethod
    def _enum_value(value: Any) -> str:
        return str(getattr(value, "value", value))

    def load(self, route: Any, context_plan: ContextPlan) -> ContextBundle:
        """物化 required planning Context，并登记 optional / executor-owned Context。"""

        mismatch = self._validate_route_binding(route, context_plan)
        if mismatch:
            return ContextBundle(
                context_plan=context_plan,
                status=ContextBundleStatus.BLOCKED,
                warnings=(mismatch,),
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
                items.extend(self._load_semantic(route, requirement))
            elif requirement.source is ContextSource.METADATA:
                items.extend(self._load_metadata(route, requirement))
            elif requirement.source is ContextSource.SKILL:
                items.append(self._load_skill(route, requirement))
            elif requirement.source in {ContextSource.RUNTIME, ContextSource.KNOWLEDGE}:
                items.append(self._executor_owned(route, requirement))
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

        return self._finalize(context_plan, tuple(items))

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
    ) -> list[ContextItem]:
        metrics = self._metric_targets(route)
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
            payload = self.repo.metric_context(metric)
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

            items.append(
                ContextItem(
                    source=ContextSource.SEMANTIC,
                    key=f"metric:{metric}",
                    required=True,
                    status=ContextItemStatus.LOADED,
                    payload=payload,
                    authority="dbt_metricflow",
                    evidence_mode="STATIC_GOVERNED_SEMANTIC",
                    estimated_tokens=self.budget.estimate(payload),
                )
            )
        return items

    def _load_metadata(
        self,
        route: Any,
        requirement: ContextRequirement,
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
            dataset = self.repo.dataset_context(target)
            if dataset is None:
                return [self._metadata_missing(target, required=True)]

            payload: dict[str, Any] = {"dataset": dataset}
            if intent == "LINEAGE_QUERY":
                direction = "downstream" if "downstream" in str(getattr(route, "question", "")).casefold() or "下游" in str(getattr(route, "question", "")) else "upstream"
                payload["lineage"] = self.repo.static_lineage(
                    target,
                    direction=direction,
                    max_hops=min(2, requirement.max_items),
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
                    estimated_tokens=self.budget.estimate(payload),
                )
            ]

        if kind == "entity":
            entity = self.repo.entity_context(target)
            if entity is None:
                return [self._metadata_missing(target, required=True)]
            return [
                ContextItem(
                    source=ContextSource.METADATA,
                    key=f"entity:{target}",
                    required=True,
                    status=ContextItemStatus.LOADED,
                    payload=entity,
                    authority="dbt_metricflow+governed_metadata",
                    evidence_mode="STATIC_CONTRACT",
                    estimated_tokens=self.budget.estimate(entity),
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
    ) -> ContextItem:
        resolution = self.skills.resolve(route)
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
        return ContextItem(
            source=ContextSource.SKILL,
            key=f"skill:{skill.skill_id}",
            required=True,
            status=ContextItemStatus.LOADED,
            payload=payload,
            authority="governed_skill_registry",
            evidence_mode="STATIC_SKILL_CONTRACT",
            estimated_tokens=self.budget.estimate(payload),
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
