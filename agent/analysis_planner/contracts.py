"""Governed Analysis Planner（受治理分析规划器）的结构化契约。

职责边界：
- Router 已确认这是 ANALYSIS；
- Context Planner 已确认允许加载 Semantic + Skill Context；
- Skill Registry 已决定“用哪套分析方法”；
- Analysis Planner 只把 Skill 编译成受治理的执行单元，不直接执行查询。

这里不允许 Skill 直接携带 SQL，也不允许重新定义 Metric / Join / Dimension Path。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agent.time_context import TimeComparisonContext


class AnalysisPlanStatus(str, Enum):
    READY = "READY"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class AnalysisUnitKind(str, Enum):
    """Analysis Planner 可以生成的有限执行单元。"""

    TIME_COMPARISON = "TIME_COMPARISON"
    BREAKDOWN = "BREAKDOWN"
    EVIDENCE_SUMMARY = "EVIDENCE_SUMMARY"


@dataclass(frozen=True)
class AnalysisUnit:
    """一个可由后续 Analysis Executor 执行的受治理单元。"""

    unit_id: str
    kind: AnalysisUnitKind
    skill_step_id: str
    required: bool
    authority: str
    compiled_plan: Any | None = None
    depends_on: tuple[str, ...] = ()
    purpose: str = ""

    def to_dict(self) -> dict[str, Any]:
        plan = self.compiled_plan
        if hasattr(plan, "to_dict"):
            plan = plan.to_dict()
        return {
            "unit_id": self.unit_id,
            "kind": self.kind.value,
            "skill_step_id": self.skill_step_id,
            "required": self.required,
            "authority": self.authority,
            "compiled_plan": plan,
            "depends_on": list(self.depends_on),
            "purpose": self.purpose,
        }


@dataclass
class AnalysisPlan:
    """Skill 编译后的完整分析计划。"""

    status: AnalysisPlanStatus
    question: str
    target_metric: str | None = None
    skill_id: str | None = None
    comparison: TimeComparisonContext | None = None
    units: tuple[AnalysisUnit, ...] = ()
    warnings: list[str] = field(default_factory=list)

    @property
    def executable(self) -> bool:
        return self.status is AnalysisPlanStatus.READY and bool(self.units)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "question": self.question,
            "target_metric": self.target_metric,
            "skill_id": self.skill_id,
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "units": [unit.to_dict() for unit in self.units],
            "warnings": list(self.warnings),
        }
