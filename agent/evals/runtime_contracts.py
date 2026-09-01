"""Runtime Golden Eval（运行时黄金结果评估）的结构化契约。

V1 先使用可复现 Synthetic MetricFlow Fixture（合成测试数据）：
- 不是生产业务数据；
- 不代表真实生产准确率；
- 用来验证 Router -> Semantic Planner -> MetricFlow Executor ->
  Unified Agent Runtime -> Claim Ledger -> Answer Validator 这一整条链没有把结果算坏。

真正的 Live Golden Eval 必须使用独立审批的生产/预生产 Golden Manifest，
不能直接把 synthetic expected value 当生产基准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RuntimeGoldenStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    STALE = "STALE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class RuntimeGoldenCase:
    case_id: str
    question: str
    expected_columns: tuple[str, ...]
    expected_rows: tuple[dict[str, str], ...]
    expected_answer_status: str = "ANSWERED"
    expected_evidence: str = "RUNTIME_VERIFIED"
    expected_validation: str = "METRICFLOW_EXPLAIN_AND_QUERY_PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case_id,
            "question": self.question,
            "expected_columns": list(self.expected_columns),
            "expected_rows": [dict(row) for row in self.expected_rows],
            "expected_answer_status": self.expected_answer_status,
            "expected_evidence": self.expected_evidence,
            "expected_validation": self.expected_validation,
        }


@dataclass(frozen=True)
class RuntimeGoldenCheck:
    name: str
    passed: bool
    expected: Any
    actual: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
        }


@dataclass
class RuntimeGoldenResult:
    case: RuntimeGoldenCase
    status: RuntimeGoldenStatus
    checks: tuple[RuntimeGoldenCheck, ...] = ()
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status is RuntimeGoldenStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "status": self.status.value,
            "checks": [item.to_dict() for item in self.checks],
            "warnings": list(self.warnings),
        }


@dataclass
class RuntimeGoldenReport:
    results: tuple[RuntimeGoldenResult, ...]
    mode: str
    fixture_path: str
    manifest_path: str

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.results if item.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "fixture_path": self.fixture_path,
            "manifest_path": self.manifest_path,
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "pass_rate": self.pass_rate,
            },
            "results": [item.to_dict() for item in self.results],
        }
