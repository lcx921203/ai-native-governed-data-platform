"""Production-style Governed Single Agent Runtime（受治理单主智能体运行时）。

这一层只负责编排，不拥有新的指标、SQL、RAG、Runtime Fact 或业务规则。

非分析请求：
    Router
      -> Context Planner
      -> Context Loader
      -> Existing ToolPlan Executor
      -> Existing Response Composer / Claim Ledger
      -> Renderer
      -> Answer Validator

分析请求：
    Router(ANALYSIS)
      -> Context Planner
      -> Context Loader
      -> Analysis Planner
      -> Analysis Executor
      -> Analysis Validation / bounded retry
      -> Claim Ledger
      -> Renderer
      -> Answer Validator
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml

from agent.analysis_planner import (
    AnalysisPlanStatus,
    GovernedAnalysisExecutor,
    GovernedAnalysisPlanner,
)
from agent.context import (
    ContextBundleStatus,
    GovernedContextLoader,
    GovernedContextPlanner,
)
from agent.response import (
    AnswerStatus,
    GovernedResponseComposer,
    render_deterministic,
    validate_answer_draft,
)
from agent.router import (
    DeterministicToolRouter,
    GovernedPlanExecutor,
    Intent,
    PlanStatus,
)

from .contracts import AgentRunResult, AgentRuntimeStatus, RuntimeStage
from .response import GovernedRuntimeResponseComposer


class GovernedAgentRuntime:
    """项目唯一的主 Agent Runtime 入口。"""

    def __init__(
        self,
        project_root: Path | str,
        *,
        router: Any | None = None,
        context_planner: Any | None = None,
        context_loader: Any | None = None,
        plan_executor: Any | None = None,
        analysis_planner: Any | None = None,
        analysis_executor: Any | None = None,
        response_composer: Any | None = None,
        runtime_response_composer: Any | None = None,
        renderer: Callable[[Any], Any] | None = None,
        answer_validator: Callable[[Any, Any], bool] | None = None,
    ):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/agent_runtime_policy.yml").read_text(encoding="utf-8")
        )

        self.router = router or DeterministicToolRouter(self.root)
        self.context_planner = context_planner or GovernedContextPlanner(self.root)
        self.context_loader = context_loader or GovernedContextLoader(self.root)
        self.plan_executor = plan_executor or GovernedPlanExecutor(self.root)
        self.analysis_planner = analysis_planner or GovernedAnalysisPlanner(self.root)
        self.analysis_executor = analysis_executor or GovernedAnalysisExecutor(self.root)
        self.response_composer = response_composer or GovernedResponseComposer(self.root)
        self.runtime_response_composer = (
            runtime_response_composer or GovernedRuntimeResponseComposer(self.root)
        )
        self.renderer = renderer or render_deterministic
        self.answer_validator = answer_validator or validate_answer_draft

    def run(self, question: str) -> AgentRunResult:
        """执行一次完整受治理 Agent 调用。"""

        stages: list[RuntimeStage] = []

        route = self.router.plan(question)
        stages.append(RuntimeStage("router", route.status.value, route.intent.value))

        # Router 已经 BLOCKED 时，不再构造无意义 Context。
        if route.status is PlanStatus.BLOCKED:
            execution = self.plan_executor.execute(route)
            stages.append(RuntimeStage("executor", execution.status.value, "router_blocked"))
            envelope = self.response_composer.compose(execution)
            stages.append(RuntimeStage("claim_ledger", envelope.status.value))
            return self._finalize(
                question,
                route=route,
                execution=execution,
                envelope=envelope,
                stages=stages,
            )

        context_plan = self.context_planner.plan(route)
        stages.append(
            RuntimeStage(
                "context_planner",
                "PLANNED" if not context_plan.warnings else "PLANNED_WITH_WARNINGS",
                ",".join(x.value for x in context_plan.required_sources()),
            )
        )

        context_bundle = self.context_loader.load(route, context_plan)
        stages.append(
            RuntimeStage(
                "context_loader",
                context_bundle.status.value,
                f"estimated_tokens={context_bundle.estimated_tokens}",
            )
        )

        if context_bundle.status in {
            ContextBundleStatus.BLOCKED,
            ContextBundleStatus.ERROR,
        }:
            answer_status = (
                AnswerStatus.ERROR
                if context_bundle.status is ContextBundleStatus.ERROR
                else AnswerStatus.BLOCKED
            )
            warnings = [
                *context_bundle.warnings,
                *[
                    warning
                    for item in context_bundle.unresolved_required()
                    for warning in item.warnings
                ],
            ]
            envelope = self.runtime_response_composer.compose_preflight_failure(
                route,
                status=answer_status,
                warnings=warnings,
            )
            stages.append(RuntimeStage("claim_ledger", envelope.status.value, "preflight_failure"))
            return self._finalize(
                question,
                route=route,
                context_plan=context_plan,
                context_bundle=context_bundle,
                envelope=envelope,
                stages=stages,
            )

        if route.intent is Intent.ANALYSIS:
            return self._run_analysis(
                question,
                route,
                context_plan,
                context_bundle,
                stages,
            )

        # 非 ANALYSIS 的所有路径继续走原有 Router ToolPlan Executor。
        execution = self.plan_executor.execute(route)
        stages.append(RuntimeStage("executor", execution.status.value, "tool_plan"))
        envelope = self.response_composer.compose(execution)
        stages.append(RuntimeStage("claim_ledger", envelope.status.value, "standard"))
        return self._finalize(
            question,
            route=route,
            context_plan=context_plan,
            context_bundle=context_bundle,
            execution=execution,
            envelope=envelope,
            stages=stages,
        )

    def _run_analysis(
        self,
        question: str,
        route: Any,
        context_plan: Any,
        context_bundle: Any,
        stages: list[RuntimeStage],
    ) -> AgentRunResult:
        """ANALYSIS 专用的 Skill -> Plan -> Execute -> Validate 链路。"""

        if route.status is not PlanStatus.PLANNING_REQUIRED:
            envelope = self.runtime_response_composer.compose_preflight_failure(
                route,
                status=AnswerStatus.BLOCKED,
                warnings=[
                    "ANALYSIS route must be PLANNING_REQUIRED before Analysis Planner execution."
                ],
            )
            stages.append(RuntimeStage("analysis_planner", "BLOCKED", "invalid_router_status"))
            stages.append(RuntimeStage("claim_ledger", envelope.status.value))
            return self._finalize(
                question,
                route=route,
                context_plan=context_plan,
                context_bundle=context_bundle,
                envelope=envelope,
                stages=stages,
            )

        analysis_plan = self.analysis_planner.plan(route, context_plan)
        stages.append(
            RuntimeStage(
                "analysis_planner",
                analysis_plan.status.value,
                f"skill={analysis_plan.skill_id or ''}; units={len(analysis_plan.units)}",
            )
        )

        if analysis_plan.status is not AnalysisPlanStatus.READY:
            envelope = self.runtime_response_composer.compose_analysis_plan_failure(
                route,
                analysis_plan,
            )
            stages.append(RuntimeStage("claim_ledger", envelope.status.value, "analysis_plan_failure"))
            return self._finalize(
                question,
                route=route,
                context_plan=context_plan,
                context_bundle=context_bundle,
                analysis_plan=analysis_plan,
                envelope=envelope,
                stages=stages,
            )

        analysis_execution = self.analysis_executor.execute_with_validation(analysis_plan)
        stages.append(
            RuntimeStage(
                "analysis_executor",
                analysis_execution.status.value,
                f"retry_rounds={analysis_execution.retry_rounds}",
            )
        )

        validation = analysis_execution.validation_result
        if validation is not None:
            stages.append(
                RuntimeStage(
                    "analysis_validation",
                    validation.decision.value,
                    f"checked_units={validation.checked_units}",
                )
            )

        envelope = self.runtime_response_composer.compose_analysis(
            route,
            analysis_execution,
        )
        stages.append(RuntimeStage("claim_ledger", envelope.status.value, "analysis"))
        return self._finalize(
            question,
            route=route,
            context_plan=context_plan,
            context_bundle=context_bundle,
            analysis_plan=analysis_plan,
            analysis_execution=analysis_execution,
            envelope=envelope,
            stages=stages,
        )

    def _finalize(
        self,
        question: str,
        *,
        route: Any,
        envelope: Any,
        stages: list[RuntimeStage],
        context_plan: Any | None = None,
        context_bundle: Any | None = None,
        analysis_plan: Any | None = None,
        execution: Any | None = None,
        analysis_execution: Any | None = None,
    ) -> AgentRunResult:
        """Renderer 之后必须经过本地 Answer Validator；验证失败即 Fail Closed。"""

        try:
            draft = self.renderer(envelope)
            stages.append(RuntimeStage("renderer", "COMPLETE"))
            validated = bool(self.answer_validator(envelope, draft))
            stages.append(RuntimeStage("answer_validator", "PASS" if validated else "BLOCKED"))
        except Exception as exc:
            stages.append(RuntimeStage("answer_validator", "ERROR", str(exc)))
            return AgentRunResult(
                question=question,
                status=AgentRuntimeStatus.ERROR,
                route=route,
                context_plan=context_plan,
                context_bundle=context_bundle,
                analysis_plan=analysis_plan,
                execution=execution,
                analysis_execution=analysis_execution,
                envelope=envelope,
                draft=None,
                answer_validated=False,
                stage_trace=tuple(stages),
                warnings=[
                    "Final answer was not returned because Answer Validator rejected the draft.",
                    str(exc),
                ],
            )

        return AgentRunResult(
            question=question,
            status=self._runtime_status(envelope.status),
            route=route,
            context_plan=context_plan,
            context_bundle=context_bundle,
            analysis_plan=analysis_plan,
            execution=execution,
            analysis_execution=analysis_execution,
            envelope=envelope,
            draft=draft,
            answer_validated=validated,
            stage_trace=tuple(stages),
            warnings=[],
        )

    @staticmethod
    def _runtime_status(answer_status: AnswerStatus) -> AgentRuntimeStatus:
        return AgentRuntimeStatus(answer_status.value)
