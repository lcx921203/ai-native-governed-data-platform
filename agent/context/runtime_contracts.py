"""Context Loader / Progressive Expansion（渐进式上下文扩展）运行契约。

Context Planner 只说“允许/需要哪些 Context”；
Context Loader 才负责把其中适合规划阶段的 Context 物化成 ContextBundle。

重要边界：
- Runtime / Knowledge 等需要真实工具执行的来源，不在规划阶段提前调用；
- optional Context 默认只登记为 NOT_LOADED；
- Progressive Expansion 只能加载 Context Plan 已允许的 optional source；
- Context Budget（上下文预算）在进入 LLM 前就被限制。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .contracts import ContextPlan, ContextSource


class ContextItemStatus(str, Enum):
    """单个 Context Item 的物化状态。"""

    LOADED = "LOADED"
    NOT_LOADED = "NOT_LOADED"
    EXECUTOR_OWNED = "EXECUTOR_OWNED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class ContextBundleStatus(str, Enum):
    """整个 Context Bundle 是否已经满足规划阶段需要。"""

    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ContextItem:
    """一个经过治理的上下文单元。"""

    source: ContextSource
    key: str
    required: bool
    status: ContextItemStatus
    payload: Any | None = None
    authority: str = ""
    evidence_mode: str = ""
    estimated_tokens: int = 0
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = self.payload
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        return {
            "source": self.source.value,
            "key": self.key,
            "required": self.required,
            "status": self.status.value,
            "payload": payload,
            "authority": self.authority,
            "evidence_mode": self.evidence_mode,
            "estimated_tokens": self.estimated_tokens,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ContextBundle:
    """Context Loader 输出。

    `EXECUTOR_OWNED` 不代表缺失：
    它表示该 Context 必须由后续受治理 Tool 在运行时获取，
    不能为了“上下文完整”而提前做重复查询。
    """

    context_plan: ContextPlan
    items: tuple[ContextItem, ...] = ()
    status: ContextBundleStatus = ContextBundleStatus.READY
    estimated_tokens: int = 0
    expansion_count: int = 0
    warnings: tuple[str, ...] = ()

    def items_for(self, source: ContextSource) -> tuple[ContextItem, ...]:
        return tuple(item for item in self.items if item.source is source)

    def loaded_items(self, source: ContextSource | None = None) -> tuple[ContextItem, ...]:
        return tuple(
            item
            for item in self.items
            if item.status is ContextItemStatus.LOADED
            and (source is None or item.source is source)
        )

    def has_loaded(self, source: ContextSource) -> bool:
        return bool(self.loaded_items(source))

    def executor_owned(self, source: ContextSource) -> bool:
        return any(
            item.source is source and item.status is ContextItemStatus.EXECUTOR_OWNED
            for item in self.items
        )

    def unresolved_required(self) -> tuple[ContextItem, ...]:
        """规划阶段真正未满足的 required Context。

        EXECUTOR_OWNED 被视为已经正确绑定到后续工具，不算缺失。
        """

        return tuple(
            item
            for item in self.items
            if item.required
            and item.status in {
                ContextItemStatus.NOT_LOADED,
                ContextItemStatus.BLOCKED,
                ContextItemStatus.ERROR,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_plan": {
                "route_intent": self.context_plan.route_intent,
                "target_kind": self.context_plan.target_kind,
                "target_id": self.context_plan.target_id,
                "required_sources": [x.value for x in self.context_plan.required_sources()],
                "optional_sources": [x.value for x in self.context_plan.optional_sources()],
                "warnings": list(self.context_plan.warnings),
            },
            "items": [item.to_dict() for item in self.items],
            "status": self.status.value,
            "estimated_tokens": self.estimated_tokens,
            "expansion_count": self.expansion_count,
            "warnings": list(self.warnings),
        }


class ContextExpansionReason(str, Enum):
    """允许触发 Progressive Context Expansion 的确定性原因。"""

    TRANSFORMATION_LOGIC_REQUIRED = "TRANSFORMATION_LOGIC_REQUIRED"
    LINEAGE_EXPLANATION_REQUIRED = "LINEAGE_EXPLANATION_REQUIRED"
    MODEL_CONTEXT_CARD_INSUFFICIENT = "MODEL_CONTEXT_CARD_INSUFFICIENT"
