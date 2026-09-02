"""执行 Deterministic Router 生成的受治理 ToolPlan。

业务逻辑：结构化查询继续调用 MetricFlow / Metadata Tool；Knowledge Intent 则固定执行
``search_knowledge → exact fetch``。Executor 只执行 Router 已批准的有限步骤，不允许 LLM
临场新增任意 SQL、文件路径、Qdrant filter 或生产写入动作。

V2 Observability：
- 记录固定/受治理 Tool 子阶段的 duration_ms；
- 不记录 Tool Arguments / Query / Payload；
- Timing 绑定到本次 PlanExecution，不使用共享 mutable `last_timing`。
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any, Callable

from agent.semantic_query import GovernedSemanticQueryPlanner, MetricFlowSemanticQueryExecutor
from agent.tools.governed_metadata import GovernedMetadataTools

from .contracts import ExecutionStatus, Intent, PlanExecution, PlanStatus


class GovernedPlanExecutor:
    """把 ToolPlan 执行为结构化结果，并保留每一步 evidence / source provenance。"""

    def __init__(self, project_root: Path | str, *, knowledge_tools: Any | None = None):
        """绑定工程根目录，并允许静态测试注入 Fake Knowledge Tools。

        Knowledge Tool 使用 lazy init，普通 Metric / Metadata 问题不会提前构造 Qdrant Runtime 依赖。
        """
        self.root = Path(project_root).resolve()
        self.tools = GovernedMetadataTools(self.root)
        self._knowledge_tools = knowledge_tools

    @staticmethod
    def _measure(
        timings: list[tuple[str, float]],
        name: str,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """执行一个 Executor 子操作并记录耗时。

        `name` 只能来自受治理 ToolPlan / 固定 Runtime Label；
        不拼接 Prompt、Metric Value、Dataset Path 或 Tool Payload。
        """

        started = perf_counter()
        value = operation(*args, **kwargs)
        timings.append(
            (
                name,
                max(0.0, (perf_counter() - started) * 1000),
            )
        )
        return value

    @staticmethod
    def _tool_timing_label(tool: Any) -> str:
        """把受治理 Tool 名压缩成 bounded ASCII Timing Label。

        即使未来 ToolPlan 来源发生变化，也不允许任意字符串直接进入 Audit Stage Name。
        """

        name = str(tool or "")
        if (
            not name
            or len(name) > 64
            or any(
                not (
                    character.isascii()
                    and (
                        character.isalnum()
                        or character == "_"
                    )
                )
                for character in name
            )
        ):
            return "unknown"
        return name

    @staticmethod
    def _compact_timings(
        timings: list[tuple[str, float]],
    ) -> tuple[tuple[str, float], ...]:
        """把一次 Execute 内同名子阶段求和，形成每请求唯一 Timing Label。"""

        totals: dict[str, float] = {}
        order: list[str] = []
        for name, duration_ms in timings:
            if name not in totals:
                totals[name] = 0.0
                order.append(name)
            totals[name] += max(0.0, float(duration_ms))
        return tuple(
            (name, totals[name])
            for name in order
        )

    def _execution(
        self,
        plan: Any,
        status: ExecutionStatus,
        timings: list[tuple[str, float]],
        results: list[dict[str, Any]] | None = None,
        warnings: list[str] | None = None,
    ) -> PlanExecution:
        """统一构造 Execution，并把本次局部 Timing 附着在返回值上。"""

        return PlanExecution(
            plan=plan,
            status=status,
            results=list(results or []),
            warnings=list(warnings or []),
            substage_timings=self._compact_timings(timings),
        )

    def _knowledge(self):
        """按需构造 ``GovernedKnowledgeTools``；真实检索仍受 Phase 7B Runtime Gate 控制。"""
        if self._knowledge_tools is None:
            from agent.knowledge.tools import GovernedKnowledgeTools

            self._knowledge_tools = GovernedKnowledgeTools(self.root)
        return self._knowledge_tools

    @staticmethod
    def _map_status(status: str, *, needs_discovery: bool) -> ExecutionStatus:
        """把各 Tool 的稳定字符串状态映射成统一 ``ExecutionStatus``。"""
        mapping = {
            "OK": ExecutionStatus.COMPLETE,
            "ANSWERED": ExecutionStatus.COMPLETE,
            "COMPLETE": ExecutionStatus.COMPLETE,
            "RESOLVED": ExecutionStatus.COMPLETE,
            "DEFERRED": ExecutionStatus.DEFERRED,
            "BLOCKED": ExecutionStatus.BLOCKED,
            "ERROR": ExecutionStatus.ERROR,
            "CLARIFICATION_REQUIRED": ExecutionStatus.CLARIFICATION_REQUIRED,
            "NOT_FOUND": ExecutionStatus.NEEDS_DISCOVERY if needs_discovery else ExecutionStatus.STOPPED,
        }
        return mapping.get(status, ExecutionStatus.STOPPED)

    def _execute_exact_knowledge_fetches(
        self,
        results: list[dict[str, Any]],
        *,
        search_result_index: int,
        top_n: int,
    ) -> list[dict[str, Any]]:
        """根据之前 Search 的 exact ``chunk_id`` 取回有限数量完整切片。

        输入只允许引用当前 execution 里已经产生的 Search Result；不能接受模型提供文件路径。
        ``top_n`` 额外限制为 1–3，避免一次问题无限扩张上下文。
        """
        if not 1 <= top_n <= 3:
            return [
                {
                    "tool": "fetch_knowledge",
                    "status": "ERROR",
                    "evidence": "STATIC_CONTRACT",
                    "payload": {},
                    "warnings": ["knowledge fetch top_n outside governed range"],
                    "sources": [],
                }
            ]
        if search_result_index < 0 or search_result_index >= len(results):
            return [
                {
                    "tool": "fetch_knowledge",
                    "status": "ERROR",
                    "evidence": "STATIC_CONTRACT",
                    "payload": {},
                    "warnings": ["knowledge search result reference is invalid"],
                    "sources": [],
                }
            ]
        search = results[search_result_index]
        if search.get("tool") != "search_knowledge" or search.get("status") != "ANSWERED":
            return []
        hits = (search.get("payload") or {}).get("results") or []
        fetched = []
        for hit in hits[:top_n]:
            chunk_id = str(hit.get("chunk_id", ""))
            if not chunk_id:
                continue
            fetched.append(self._knowledge().fetch_knowledge(chunk_id=chunk_id))
        return fetched

    def execute(self, plan) -> PlanExecution:
        """执行受治理计划；遇到 BLOCKED / ERROR / DEFERRED 等门禁状态按 step contract 停止。

        ANALYSIS 的 ``PLANNING_REQUIRED`` 代表 Router 已完成意图识别，但还没有经过
        Context Planner + Skill Registry + Analysis Planner。旧 Executor 必须显式 DEFER，
        不能把“零步骤”误判成 COMPLETE。
        """

        timings: list[tuple[str, float]] = []
        preflight_started = perf_counter()

        if plan.status is PlanStatus.BLOCKED:
            timings.append(
                (
                    "preflight",
                    max(0.0, (perf_counter() - preflight_started) * 1000),
                )
            )
            return self._execution(
                plan,
                ExecutionStatus.BLOCKED,
                timings,
                warnings=list(plan.warnings),
            )

        if plan.status is PlanStatus.PLANNING_REQUIRED:
            timings.append(
                (
                    "preflight",
                    max(0.0, (perf_counter() - preflight_started) * 1000),
                )
            )
            return self._execution(
                plan,
                ExecutionStatus.DEFERRED,
                timings,
                warnings=[
                    *list(plan.warnings),
                    "Analysis plan has not been compiled by the governed Analysis Planner yet.",
                ],
            )

        if plan.status not in {PlanStatus.PLANNED, PlanStatus.NEEDS_DISCOVERY}:
            timings.append(
                (
                    "preflight",
                    max(0.0, (perf_counter() - preflight_started) * 1000),
                )
            )
            return self._execution(
                plan,
                ExecutionStatus.STOPPED,
                timings,
                warnings=list(plan.warnings),
            )

        timings.append(
            (
                "preflight",
                max(0.0, (perf_counter() - preflight_started) * 1000),
            )
        )

        results: list[dict[str, Any]] = []
        final = (
            ExecutionStatus.NEEDS_DISCOVERY
            if plan.status is PlanStatus.NEEDS_DISCOVERY
            else ExecutionStatus.COMPLETE
        )

        for step in plan.steps:
            if step.tool == "fetch_top_knowledge_hits":
                fetched = self._measure(
                    timings,
                    "tool.fetch_top_knowledge_hits.execute",
                    self._execute_exact_knowledge_fetches,
                    results,
                    **step.arguments,
                )
                results.extend(fetched)
                if fetched:
                    final = self._measure(
                        timings,
                        "status_mapping",
                        self._map_status,
                        fetched[-1].get("status", "ERROR"),
                        needs_discovery=False,
                    )
                elif plan.intent is Intent.KNOWLEDGE_QUERY:
                    final = ExecutionStatus.STOPPED
                if final in {
                    ExecutionStatus.ERROR,
                    ExecutionStatus.BLOCKED,
                    ExecutionStatus.DEFERRED,
                }:
                    break
                continue

            if step.tool in {"query_semantic_metric", "query_semantic_metrics"}:
                planner = self._measure(
                    timings,
                    "semantic_query.planner_init",
                    GovernedSemanticQueryPlanner,
                    self.root,
                )
                if step.tool == "query_semantic_metric":
                    semantic_plan = self._measure(
                        timings,
                        "semantic_query.plan",
                        planner.plan,
                        metric=step.arguments["metric"],
                        question=step.arguments["question"],
                        limit=step.arguments.get("limit"),
                    )
                else:
                    semantic_plan = self._measure(
                        timings,
                        "semantic_query.plan",
                        planner.plan_metrics,
                        metrics=step.arguments["metrics"],
                        question=step.arguments["question"],
                        limit=step.arguments.get("limit"),
                    )

                semantic_executor = self._measure(
                    timings,
                    "semantic_query.executor_init",
                    MetricFlowSemanticQueryExecutor,
                    self.root,
                )
                result = self._measure(
                    timings,
                    "semantic_query.execute",
                    semantic_executor.execute,
                    semantic_plan,
                )
                item = {
                    "tool": step.tool,
                    "status": result.status.value,
                    "evidence": result.evidence,
                    "query": step.arguments,
                    "payload": result.to_dict(),
                    "warnings": list(result.warnings),
                    "sources": [],
                }
            elif step.tool == "search_knowledge":
                item = self._measure(
                    timings,
                    "tool.search_knowledge.execute",
                    self._knowledge().search_knowledge,
                    **step.arguments,
                )
            elif hasattr(self.tools, step.tool):
                item = self._measure(
                    timings,
                    f"tool.{self._tool_timing_label(step.tool)}.execute",
                    getattr(self.tools, step.tool),
                    **step.arguments,
                )
            else:
                item = {
                    "tool": step.tool,
                    "status": "ERROR",
                    "evidence": "STATIC_CONTRACT",
                    "payload": {},
                    "warnings": ["Unknown governed tool"],
                    "sources": [],
                }

            results.append(item)
            status = item.get("status", "ERROR")
            final = self._measure(
                timings,
                "status_mapping",
                self._map_status,
                status,
                needs_discovery=plan.status is PlanStatus.NEEDS_DISCOVERY,
            )
            if status in step.stop_on_status:
                break

        if plan.status is PlanStatus.NEEDS_DISCOVERY and final is ExecutionStatus.COMPLETE:
            final = ExecutionStatus.NEEDS_DISCOVERY

        return self._execution(
            plan,
            final,
            timings,
            results=results,
            warnings=[],
        )
