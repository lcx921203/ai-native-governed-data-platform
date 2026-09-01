"""Deterministic Agent Eval Runner（确定性智能体评估运行器）。

Static Regression 模式故意不执行 MetricFlow / Qdrant / Dagster：
它用于每次代码提交时快速验证“受治理决策链是否回归”。

Runtime / Golden Result Accuracy 会作为下一层 Eval：
只有存在可复现测试数据集、固定结果和运行环境时才开启，避免伪造业务数值。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from agent.analysis_planner import GovernedAnalysisPlanner
from agent.context import GovernedContextPlanner
from agent.router import DeterministicToolRouter, Intent, PlanStatus

from .contracts import AgentEvalReport, AgentEvalResult, EvalCaseStatus
from .loader import GovernedEvalSuiteLoader
from .scorers import score_analysis, score_context, score_route


class GovernedAgentEvalRunner:
    """运行 repository-owned Agent regression suites。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/agent_eval_policy.yml").read_text(
                encoding="utf-8"
            )
        )
        self.loader = GovernedEvalSuiteLoader(self.root)
        self.router = DeterministicToolRouter(self.root)
        self.context_planner = GovernedContextPlanner(self.root)
        self.analysis_planner = GovernedAnalysisPlanner(self.root)

    def run(self, suites: Iterable[str] | None = None) -> AgentEvalReport:
        results: list[AgentEvalResult] = []
        for case in self.loader.load(suites):
            results.append(self._run_case(case))
        return AgentEvalReport(
            results=tuple(results),
            policy_version=int(self.policy["version"]),
            mode="STATIC_REGRESSION",
        )

    def assert_gate(self, report: AgentEvalReport) -> None:
        """CI Regression Gate：关键用例失败或整体低于阈值时直接失败。"""

        limits = self.policy["gates"]
        if bool(limits.get("fail_on_critical_case", True)) and report.critical_failures:
            ids = ", ".join(item.case.case_id for item in report.critical_failures)
            raise AssertionError(f"Critical Agent Eval case(s) failed: {ids}")

        minimum = float(limits.get("minimum_static_pass_rate", 1.0))
        if report.pass_rate < minimum:
            raise AssertionError(
                f"Agent Eval pass_rate={report.pass_rate:.4f} below required {minimum:.4f}"
            )

    def _run_case(self, case):
        route = None
        context_plan = None
        analysis_plan = None
        try:
            route = self.router.plan(case.question)
            checks = score_route(case, route)

            # BLOCKED 是 Router 的最终治理结论，不能为了评估而绕过继续规划 Context。
            if route.status is not PlanStatus.BLOCKED:
                context_plan = self.context_planner.plan(route)
                checks.extend(score_context(case, context_plan))

                if route.intent is Intent.ANALYSIS:
                    analysis_plan = self.analysis_planner.plan(route, context_plan)
                    checks.extend(score_analysis(case, analysis_plan))
            else:
                # 若 case 明确要求 context/analysis，而实际被 Router BLOCKED，
                # scorer 仍应该暴露缺失，而不是静默跳过。
                checks.extend(score_context(case, None))
                checks.extend(score_analysis(case, None))

            status = (
                EvalCaseStatus.PASS
                if checks and all(item.passed for item in checks)
                else EvalCaseStatus.FAIL
            )
            return AgentEvalResult(
                case=case,
                status=status,
                checks=tuple(checks),
                observed={
                    "route": route.to_dict() if hasattr(route, "to_dict") else None,
                    "context_plan": {
                        "route_intent": context_plan.route_intent,
                        "required_context": [
                            x.value for x in context_plan.required_sources()
                        ],
                        "optional_context": [
                            x.value for x in context_plan.optional_sources()
                        ],
                        "warnings": list(context_plan.warnings),
                    }
                    if context_plan is not None
                    else None,
                    "analysis_plan": (
                        analysis_plan.to_dict()
                        if analysis_plan is not None
                        and hasattr(analysis_plan, "to_dict")
                        else None
                    ),
                },
            )
        except Exception as exc:
            return AgentEvalResult(
                case=case,
                status=EvalCaseStatus.ERROR,
                observed={
                    "route": route.to_dict() if route is not None and hasattr(route, "to_dict") else None,
                },
                warnings=[f"{type(exc).__name__}: {exc}"],
            )
