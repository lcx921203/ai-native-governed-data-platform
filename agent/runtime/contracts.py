"""Single Agent Runtime（单主智能体运行时）的结构化契约。

Runtime 的目标不是创造新的业务能力，而是把已经存在的受治理组件串成一条
唯一、可追踪、可验证的执行链。

主链路：
    Router
      -> Context Planner
      -> Context Loader
      -> Planner
      -> Executor
      -> Validation
      -> Claim Ledger
      -> Renderer
      -> Answer Validator

Router / Planner / Executor / Validator 都是同一个 Runtime 的阶段，
不是多个彼此自治的 Agent。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentRuntimeStatus(str, Enum):
    ANSWERED = "ANSWERED"
    PARTIAL = "PARTIAL"
    NEEDS_DISCOVERY = "NEEDS_DISCOVERY"
    DEFERRED = "DEFERRED"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


@dataclass(frozen=True)
class RuntimeStage:
    """一个 Runtime 阶段的有界状态记录。

    这里只保存阶段名和状态，不在这里提前做完整 tracing / cost accounting。
    后续 Observability 会在这个基础上增加 latency / token / cost。
    """

    stage: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class AgentRunResult:
    """一次完整 Agent Runtime 调用的返回对象。"""

    question: str
    status: AgentRuntimeStatus
    route: Any | None = None
    context_plan: Any | None = None
    context_bundle: Any | None = None
    analysis_plan: Any | None = None
    execution: Any | None = None
    analysis_execution: Any | None = None
    envelope: Any | None = None
    draft: Any | None = None
    answer_validated: bool = False
    stage_trace: tuple[RuntimeStage, ...] = ()
    warnings: list[str] = field(default_factory=list)

    @property
    def answer(self) -> str:
        return str(getattr(self.draft, "answer", "") or "")

    def to_dict(self) -> dict[str, Any]:
        def dump(value: Any) -> Any:
            if value is None:
                return None
            if hasattr(value, "to_dict"):
                return value.to_dict()
            return value

        return {
            "question": self.question,
            "status": self.status.value,
            "route": dump(self.route),
            "context_plan": dump(self.context_plan),
            "context_bundle": dump(self.context_bundle),
            "analysis_plan": dump(self.analysis_plan),
            "execution": dump(self.execution),
            "analysis_execution": dump(self.analysis_execution),
            "envelope": dump(self.envelope),
            "draft": {
                "answer": getattr(self.draft, "answer", ""),
                "used_claim_ids": list(getattr(self.draft, "used_claim_ids", ()) or ()),
                "acknowledged_limitations": list(
                    getattr(self.draft, "acknowledged_limitations", ()) or ()
                ),
            }
            if self.draft is not None
            else None,
            "answer_validated": self.answer_validated,
            "stage_trace": [item.to_dict() for item in self.stage_trace],
            "warnings": list(self.warnings),
        }
