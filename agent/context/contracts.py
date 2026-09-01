"""Context Planner（上下文规划器）的结构化契约。

职责边界：
- Router 已经判断“用户想做什么、应该走哪条受治理路径”；
- Context Planner 只判断“完成这条路径需要哪些上下文”；
- 本模块不读取 MetricFlow / DataHub / RAG / 源码，也不执行查询；
- 本模块不重新做 Intent（意图）识别，避免出现两套路由结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ContextSource(str, Enum):
    """Agent 可按需加载的上下文来源。"""

    # Metric / Entity / Dimension / Business Time 等受治理语义。
    SEMANTIC = "semantic"

    # Dataset / Owner / Domain / Lineage 等元数据。
    METADATA = "metadata"

    # Dagster Run / Freshness / Partition 等运行时事实。
    RUNTIME = "runtime"

    # Knowledge RAG 中的 Runbook、设计决策、业务解释。
    KNOWLEDGE = "knowledge"

    # 某类分析问题的标准分析步骤。下一步接 Skill Registry。
    SKILL = "skill"

    # 从 dbt / SQL / YAML 提炼的代码事实。后续接 Model Context Card。
    CODE = "code"

    # 用户偏好、受控纠正等长期上下文。当前先保留扩展位置。
    MEMORY = "memory"


@dataclass(frozen=True)
class ContextRequirement:
    """一次请求对某一类 Context 的需求。"""

    source: ContextSource
    required: bool
    max_items: int
    reason: str


@dataclass(frozen=True)
class ContextPlan:
    """Context Planner 的输出。

    注意：这是“读取计划”，不是实际 Context 内容。
    真正的数据读取由后续 Context Loader 负责。
    """

    route_intent: str
    target_kind: str | None
    target_id: str | None
    requirements: tuple[ContextRequirement, ...]
    warnings: tuple[str, ...] = ()

    def requires(self, source: ContextSource) -> bool:
        """当前计划是否需要指定来源（无论 required / optional）。"""

        return any(item.source is source for item in self.requirements)

    def required_sources(self) -> tuple[ContextSource, ...]:
        """返回所有强制上下文来源。"""

        return tuple(
            item.source
            for item in self.requirements
            if item.required
        )

    def optional_sources(self) -> tuple[ContextSource, ...]:
        """返回所有按需加载的可选上下文来源。"""

        return tuple(
            item.source
            for item in self.requirements
            if not item.required
        )
