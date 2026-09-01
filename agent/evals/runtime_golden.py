"""Full-path Synthetic Runtime Golden Eval。

验证链路：
    User Question
      -> GovernedAgentRuntime
      -> Router
      -> Context Planner / Loader
      -> Governed Semantic Planner
      -> MetricFlow Executor
      -> Synthetic MetricFlow Runner
      -> Claim Ledger
      -> Renderer
      -> Answer Validator
      -> Golden Comparator

注意：Synthetic Runner 只证明“Agent 工程链路和受治理结果投影没有回归”，
不替代真实 MetricFlow + Warehouse 的 Live Golden Eval。
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from agent.code_context import git_blob_sha
from agent.response import AnswerStatus
from agent.router import ExecutionStatus, PlanExecution, PlanStatus
from agent.runtime import GovernedAgentRuntime
from agent.semantic_query import (
    GovernedSemanticQueryPlanner,
    MetricFlowSemanticQueryExecutor,
)

from .runtime_contracts import (
    RuntimeGoldenCase,
    RuntimeGoldenCheck,
    RuntimeGoldenReport,
    RuntimeGoldenResult,
    RuntimeGoldenStatus,
)
from .synthetic_metricflow import SyntheticMetricFlowRunner


class _GoldenSemanticPlanExecutor:
    """Eval-only ToolPlan Executor，只允许 Semantic Query Tool。"""

    def __init__(self, project_root: Path, semantic_executor: MetricFlowSemanticQueryExecutor):
        self.root = project_root
        self.semantic_executor = semantic_executor
        self.planner = GovernedSemanticQueryPlanner(project_root)

    def execute(self, plan) -> PlanExecution:
        if plan.status is PlanStatus.BLOCKED:
            return PlanExecution(
                plan,
                ExecutionStatus.BLOCKED,
                warnings=list(plan.warnings),
            )
        if plan.status is not PlanStatus.PLANNED:
            return PlanExecution(
                plan,
                ExecutionStatus.STOPPED,
                warnings=[*list(plan.warnings), "Runtime Golden executor requires PLANNED semantic query."],
            )

        results: list[dict[str, Any]] = []
        final = ExecutionStatus.COMPLETE
        for step in plan.steps:
            if step.tool == "query_semantic_metric":
                semantic_plan = self.planner.plan(
                    metric=step.arguments["metric"],
                    question=step.arguments["question"],
                    limit=step.arguments.get("limit"),
                )
            elif step.tool == "query_semantic_metrics":
                semantic_plan = self.planner.plan_metrics(
                    metrics=step.arguments["metrics"],
                    question=step.arguments["question"],
                    limit=step.arguments.get("limit"),
                )
            else:
                results.append(
                    {
                        "tool": step.tool,
                        "status": "BLOCKED",
                        "evidence": "STATIC_CONTRACT",
                        "payload": {},
                        "warnings": ["Runtime Golden executor accepts semantic query tools only."],
                        "sources": [],
                    }
                )
                final = ExecutionStatus.BLOCKED
                break

            result = self.semantic_executor.execute(semantic_plan)
            results.append(
                {
                    "tool": step.tool,
                    "status": result.status.value,
                    "evidence": result.evidence,
                    "query": dict(step.arguments),
                    "payload": result.to_dict(),
                    "warnings": list(result.warnings),
                    "sources": [],
                }
            )
            mapping = {
                "COMPLETE": ExecutionStatus.COMPLETE,
                "DEFERRED": ExecutionStatus.DEFERRED,
                "BLOCKED": ExecutionStatus.BLOCKED,
                "ERROR": ExecutionStatus.ERROR,
                "CLARIFICATION_REQUIRED": ExecutionStatus.CLARIFICATION_REQUIRED,
            }
            final = mapping.get(result.status.value, ExecutionStatus.STOPPED)
            if final is not ExecutionStatus.COMPLETE:
                break

        return PlanExecution(plan, final, results=results, warnings=[])


class GovernedRuntimeGoldenEvalRunner:
    """运行 repository-owned synthetic Runtime Golden cases。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/runtime_golden_eval_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        self.manifest_path = self.root / self.policy["paths"]["manifest"]
        self.manifest = yaml.safe_load(
            self.manifest_path.read_text(encoding="utf-8")
        )
        self.fixture_path = self.root / self.manifest["fixture"]["path"]

    def load_cases(self) -> tuple[RuntimeGoldenCase, ...]:
        return tuple(
            RuntimeGoldenCase(
                case_id=str(item["id"]),
                question=str(item["question"]),
                expected_columns=tuple(str(x) for x in item["expect"]["columns"]),
                expected_rows=tuple(
                    {str(k): str(v) for k, v in row.items()}
                    for row in item["expect"]["rows"]
                ),
                expected_answer_status=str(
                    item["expect"].get("answer_status", "ANSWERED")
                ),
                expected_evidence=str(
                    item["expect"].get("evidence", "RUNTIME_VERIFIED")
                ),
                expected_validation=str(
                    item["expect"].get(
                        "validation",
                        "METRICFLOW_EXPLAIN_AND_QUERY_PASS",
                    )
                ),
            )
            for item in self.manifest.get("cases", ())
        )

    def run(self) -> RuntimeGoldenReport:
        stale = self._stale_sources()
        if stale:
            results = tuple(
                RuntimeGoldenResult(
                    case=case,
                    status=RuntimeGoldenStatus.STALE,
                    warnings=stale,
                )
                for case in self.load_cases()
            )
            return self._report(results)

        with self._synthetic_runtime() as runtime:
            results = tuple(self._run_case(runtime, case) for case in self.load_cases())
        return self._report(results)

    def assert_gate(self, report: RuntimeGoldenReport) -> None:
        if report.pass_rate < float(self.policy["gates"]["minimum_pass_rate"]):
            failed = ", ".join(
                item.case.case_id for item in report.results if not item.passed
            )
            raise AssertionError(
                f"Runtime Golden Eval pass_rate={report.pass_rate:.4f}; failed={failed}"
            )

    def _run_case(
        self,
        runtime: GovernedAgentRuntime,
        case: RuntimeGoldenCase,
    ) -> RuntimeGoldenResult:
        try:
            run = runtime.run(case.question)
            semantic = self._semantic_result(run)
            if semantic is None:
                return RuntimeGoldenResult(
                    case,
                    RuntimeGoldenStatus.FAIL,
                    warnings=["Unified Runtime produced no semantic execution result."],
                )

            actual_rows = self._canonical_rows(
                semantic.get("rows") or [],
                case.expected_columns,
            )
            expected_rows = self._canonical_rows(
                list(case.expected_rows),
                case.expected_columns,
            )

            checks = (
                self._check(
                    "answer_status",
                    case.expected_answer_status,
                    run.status.value,
                ),
                self._check(
                    "answer_validated",
                    True,
                    bool(run.answer_validated),
                ),
                self._check(
                    "evidence",
                    case.expected_evidence,
                    str((run.execution.results[0] or {}).get("evidence", "")),
                ),
                self._check(
                    "validation",
                    case.expected_validation,
                    str(semantic.get("validation", "")),
                ),
                self._check(
                    "columns",
                    list(case.expected_columns),
                    list(semantic.get("columns") or []),
                ),
                self._check(
                    "rows",
                    expected_rows,
                    actual_rows,
                ),
                self._check(
                    "stage_trace",
                    [
                        "router",
                        "context_planner",
                        "context_loader",
                        "executor",
                        "claim_ledger",
                        "renderer",
                        "answer_validator",
                    ],
                    [item.stage for item in run.stage_trace],
                ),
            )

            return RuntimeGoldenResult(
                case=case,
                status=(
                    RuntimeGoldenStatus.PASS
                    if all(item.passed for item in checks)
                    else RuntimeGoldenStatus.FAIL
                ),
                checks=checks,
            )
        except Exception as exc:
            return RuntimeGoldenResult(
                case=case,
                status=RuntimeGoldenStatus.ERROR,
                warnings=[f"{type(exc).__name__}: {exc}"],
            )

    @staticmethod
    def _semantic_result(run: Any) -> dict[str, Any] | None:
        execution = getattr(run, "execution", None)
        if execution is None or not execution.results:
            return None
        payload = execution.results[0].get("payload") or {}
        return dict(payload)

    @staticmethod
    def _check(name: str, expected: Any, actual: Any) -> RuntimeGoldenCheck:
        return RuntimeGoldenCheck(
            name=name,
            passed=expected == actual,
            expected=expected,
            actual=actual,
        )

    @staticmethod
    def _canonical_value(value: Any) -> str:
        text = str(value)
        try:
            decimal = Decimal(text)
        except (InvalidOperation, ValueError):
            return text
        normalized = format(decimal.normalize(), "f")
        return "0" if normalized in {"-0", ""} else normalized

    @classmethod
    def _canonical_rows(
        cls,
        rows: list[dict[str, Any]],
        columns: tuple[str, ...],
    ) -> list[dict[str, str]]:
        canonical = [
            {
                column: cls._canonical_value(row.get(column, ""))
                for column in columns
            }
            for row in rows
        ]
        return sorted(
            canonical,
            key=lambda row: tuple(row.get(column, "") for column in columns),
        )

    def _stale_sources(self) -> list[str]:
        warnings: list[str] = []
        for item in self.manifest.get("source_fingerprints", ()):
            path = self.root / str(item["path"])
            if not path.exists():
                warnings.append(f"Golden source is missing: {item['path']}")
                continue
            actual = git_blob_sha(path.read_text(encoding="utf-8"))
            if actual != str(item["git_blob_sha"]):
                warnings.append(
                    f"Golden source changed and requires explicit review: {item['path']}"
                )

        fixture = self.manifest.get("fixture") or {}
        fixture_path = self.root / str(fixture.get("path", ""))
        if not fixture_path.exists():
            warnings.append("Runtime Golden fixture is missing.")
        else:
            actual_fixture = self._binary_git_blob_sha(fixture_path)
            if actual_fixture != str(fixture.get("git_blob_sha", "")):
                warnings.append(
                    "Runtime Golden fixture changed and requires expected-result review."
                )
        return warnings

    @staticmethod
    def _binary_git_blob_sha(path: Path) -> str:
        import hashlib

        payload = path.read_bytes()
        return hashlib.sha1(
            f"blob {len(payload)}\0".encode("utf-8") + payload
        ).hexdigest()

    @contextmanager
    def _synthetic_runtime(self):
        """显式打开 MetricFlow runtime gate，但只注入 synthetic subprocess runner。"""

        policy = yaml.safe_load(
            (self.root / "agent/contracts/semantic_query_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        allow_env = str(policy["runtime"]["allow_env"])
        bin_env = str(policy["runtime"]["metricflow_bin_env"])

        old_allow = os.environ.get(allow_env)
        old_bin = os.environ.get(bin_env)

        with tempfile.TemporaryDirectory(prefix="agent-runtime-golden-") as temp_dir:
            fake_mf = Path(temp_dir) / "mf"
            fake_mf.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_mf.chmod(0o755)

            os.environ[allow_env] = "true"
            os.environ[bin_env] = str(fake_mf)
            try:
                synthetic = SyntheticMetricFlowRunner(self.fixture_path)
                semantic_executor = MetricFlowSemanticQueryExecutor(
                    self.root,
                    runner=synthetic,
                )
                plan_executor = _GoldenSemanticPlanExecutor(
                    self.root,
                    semantic_executor,
                )
                yield GovernedAgentRuntime(
                    self.root,
                    plan_executor=plan_executor,
                )
            finally:
                if old_allow is None:
                    os.environ.pop(allow_env, None)
                else:
                    os.environ[allow_env] = old_allow
                if old_bin is None:
                    os.environ.pop(bin_env, None)
                else:
                    os.environ[bin_env] = old_bin

    def _report(
        self,
        results: tuple[RuntimeGoldenResult, ...],
    ) -> RuntimeGoldenReport:
        return RuntimeGoldenReport(
            results=results,
            mode=str(self.manifest.get("mode", "SYNTHETIC_METRICFLOW_FIXTURE")),
            fixture_path=self.fixture_path.relative_to(self.root).as_posix(),
            manifest_path=self.manifest_path.relative_to(self.root).as_posix(),
        )
