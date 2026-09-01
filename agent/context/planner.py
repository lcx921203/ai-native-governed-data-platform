"""Route-driven Context Planner（路由驱动的上下文规划器）。

正确顺序：
    User -> Intent / Router -> Context Planner -> Planner -> Executor

设计原则：
1. Intent / Router 在前，Context Planner 在后；
2. Context Planner 不重新分析用户问题来改变 Intent；
3. 上下文按最小必要原则加载，避免“上下文越多越好”；
4. 未登记的新 Intent 默认 Fail Closed（失败关闭），不偷偷加载全量上下文。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .contracts import ContextPlan, ContextRequirement, ContextSource


class GovernedContextPlanner:
    """根据 Router 已经确定的结果生成最小必要 Context Plan。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy_path = self.root / "agent/contracts/context_planner_policy.yml"
        self.policy: dict[str, Any] = yaml.safe_load(
            self.policy_path.read_text(encoding="utf-8")
        )

    @staticmethod
    def _enum_value(value: Any) -> str:
        """兼容 Enum 与普通字符串形式的 Intent。"""

        raw = getattr(value, "value", value)
        return str(raw)

    def plan(self, route: Any) -> ContextPlan:
        """根据 Router 的 ToolPlan / RouteResult 生成上下文读取计划。

        输入要求：
        - route 至少提供 intent；
        - target_kind / target_id 可以为空。

        这里故意不接收 question 参数：
        Context Planner 不允许绕过 Router 再重新判断一次 Intent。
        """

        intent = self._enum_value(getattr(route, "intent", "UNKNOWN"))
        target_kind = getattr(route, "target_kind", None)
        target_id = getattr(route, "target_id", None)

        config = (
            self.policy
            .get("intent_context", {})
            .get(intent)
        )

        # Router 新增 Intent 后，如果这里还没有受治理策略，默认不加载任何上下文。
        # 后续 Runtime 可以据此进入 BLOCKED / DEFERRED / NEEDS_POLICY。
        if config is None:
            return ContextPlan(
                route_intent=intent,
                target_kind=target_kind,
                target_id=target_id,
                requirements=(),
                warnings=(
                    f"No governed context policy is registered for intent={intent}.",
                ),
            )

        default_max_items = int(
            self.policy.get("defaults", {}).get("max_items", 5)
        )
        requirements: list[ContextRequirement] = []

        for item in config:
            requirements.append(
                ContextRequirement(
                    source=ContextSource(str(item["source"])),
                    required=bool(item.get("required", False)),
                    max_items=int(item.get("max_items", default_max_items)),
                    reason=str(item.get("reason", "")).strip(),
                )
            )

        return ContextPlan(
            route_intent=intent,
            target_kind=target_kind,
            target_id=target_id,
            requirements=tuple(requirements),
        )
