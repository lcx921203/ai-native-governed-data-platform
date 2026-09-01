"""Agent Eval（智能体评估）的结构化契约。

Eval 的目标不是“让 LLM 给 LLM 打分”，而是把可以确定性验证的行为先变成
Regression Contract（回归契约）：
- Intent / Route 是否正确；
- Target 是否绑定到受治理对象；
- Context 是否最小且符合策略；
- ANALYSIS 是否命中正确 Skill；
- Analysis Plan 是否包含预期受治理步骤；
- 危险/越权输入是否被 Fail Closed。

真实业务数值准确率属于 Runtime / Golden Result Eval，后续在有可重复测试数据集时接入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EvalCaseStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class AgentEvalCase:
    case_id: str
    suite: str
    category: str
    question: str
    critical: bool
    expect: dict[str, Any]
    source_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "suite": self.suite,
            "category": self.category,
            "question": self.question,
            "critical": self.critical,
            "expect": dict(self.expect),
            "source_path": self.source_path,
        }


@dataclass(frozen=True)
class EvalCheck:
    name: str
    passed: bool
    expected: Any
    actual: Any
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


@dataclass
class AgentEvalResult:
    case: AgentEvalCase
    status: EvalCaseStatus
    checks: tuple[EvalCheck, ...] = ()
    observed: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status is EvalCaseStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "status": self.status.value,
            "checks": [item.to_dict() for item in self.checks],
            "observed": dict(self.observed),
            "warnings": list(self.warnings),
        }


@dataclass
class AgentEvalReport:
    results: tuple[AgentEvalResult, ...]
    policy_version: int
    mode: str = "STATIC_REGRESSION"

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.results if item.status is EvalCaseStatus.PASS)

    @property
    def failed(self) -> int:
        return sum(
            1
            for item in self.results
            if item.status in {EvalCaseStatus.FAIL, EvalCaseStatus.ERROR}
        )

    @property
    def skipped(self) -> int:
        return sum(1 for item in self.results if item.status is EvalCaseStatus.SKIPPED)

    @property
    def pass_rate(self) -> float:
        measured = self.total - self.skipped
        return self.passed / measured if measured else 0.0

    @property
    def critical_failures(self) -> tuple[AgentEvalResult, ...]:
        return tuple(
            item
            for item in self.results
            if item.case.critical
            and item.status in {EvalCaseStatus.FAIL, EvalCaseStatus.ERROR}
        )

    def category_summary(self) -> dict[str, dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        categories = sorted({item.case.category for item in self.results})
        for category in categories:
            rows = [item for item in self.results if item.case.category == category]
            measured = [
                item for item in rows if item.status is not EvalCaseStatus.SKIPPED
            ]
            passed = sum(1 for item in measured if item.status is EvalCaseStatus.PASS)
            output[category] = {
                "total": len(rows),
                "measured": len(measured),
                "passed": passed,
                "pass_rate": passed / len(measured) if measured else 0.0,
            }
        return output

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "policy_version": self.policy_version,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "skipped": self.skipped,
                "pass_rate": self.pass_rate,
                "critical_failures": len(self.critical_failures),
                "categories": self.category_summary(),
            },
            "results": [item.to_dict() for item in self.results],
        }
