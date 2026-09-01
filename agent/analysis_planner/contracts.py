"""Governed Analysis Planner（受治理分析规划器）的结构化契约。

职责边界：
- Router 已确认这是 ANALYSIS；
- Context Planner 已确认允许加载 Semantic + Skill Context；
- Skill Registry 已决定“用哪套分析方法”；
- Analysis Planner 只把 Skill 编译成受治理的执行单元；
- Analysis Executor 只执行这些已编译单元，不允许临场增加任意 SQL / Tool；
- Validation 对执行证据做 PASS / RETRY / BLOCKED 判定。

这里不允许 Skill 或 Executor 重新定义 Metric / Join / Dimension Path。
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


class AnalysisUnitExecutionStatus(str, Enum):
    """单个 Analysis Unit 的运行状态。"""

    COMPLETE = "COMPLETE"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    SKIPPED = "SKIPPED"


class AnalysisExecutionStatus(str, Enum):
    """一次完整 Analysis Plan 的运行状态。"""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


@dataclass
class AnalysisUnitExecution:
    """单个执行单元的运行证据。"""

    unit_id: str
    kind: AnalysisUnitKind
    required: bool
    status: AnalysisUnitExecutionStatus
    evidence: str
    result: Any | None = None
    warnings: list[str] = field(default_factory=list)
    validation: str = ""
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        payload = self.result
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        return {
            "unit_id": self.unit_id,
            "kind": self.kind.value,
            "required": self.required,
            "status": self.status.value,
            "evidence": self.evidence,
            "result": payload,
            "warnings": list(self.warnings),
            "validation": self.validation,
            "attempt": self.attempt,
        }


@dataclass
class AnalysisExecution:
    """Analysis Executor 的完整输出；Validation Result 以结构化对象挂载。"""

    plan: AnalysisPlan
    status: AnalysisExecutionStatus
    unit_results: tuple[AnalysisUnitExecution, ...] = ()
    warnings: list[str] = field(default_factory=list)
    validation_result: Any | None = None
    retry_rounds: int = 0

    def unit_result(self, unit_id: str) -> AnalysisUnitExecution | None:
        return next((item for item in self.unit_results if item.unit_id == unit_id), None)

    def to_dict(self) -> dict[str, Any]:
        validation = self.validation_result
        if hasattr(validation, "to_dict"):
            validation = validation.to_dict()
        return {
            "status": self.status.value,
            "plan": self.plan.to_dict(),
            "unit_results": [item.to_dict() for item in self.unit_results],
            "warnings": list(self.warnings),
            "validation_result": validation,
            "retry_rounds": self.retry_rounds,
        }
