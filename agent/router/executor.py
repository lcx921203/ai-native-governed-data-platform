"""执行 Deterministic Router 生成的受治理 ToolPlan。

业务逻辑：结构化查询继续调用 MetricFlow / Metadata Tool；Knowledge Intent 则固定执行
``search_knowledge → exact fetch``。Executor 只执行 Router 已批准的有限步骤，不允许 LLM
临场新增任意 SQL、文件路径、Qdrant filter 或生产写入动作。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

    def _execute_exact_knowledge_fetches(self, results: list[dict[str, Any]], *, search_result_index: int, top_n: int) -> list[dict[str, Any]]:
        """根据之前 Search 的 exact ``chunk_id`` 取回有限数量完整切片。

        输入只允许引用当前 execution 里已经产生的 Search Result；不能接受模型提供文件路径。
        ``top_n`` 额外限制为 1–3，避免一次问题无限扩张上下文。
        """
        if not 1 <= top_n <= 3:
            return [{"tool": "fetch_knowledge", "status": "ERROR", "evidence": "STATIC_CONTRACT", "payload": {}, "warnings": ["knowledge fetch top_n outside governed range"], "sources": []}]
        if search_result_index < 0 or search_result_index >= len(results):
            return [{"tool": "fetch_knowledge", "status": "ERROR", "evidence": "STATIC_CONTRACT", "payload": {}, "warnings": ["knowledge search result reference is invalid"], "sources": []}]
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
        if plan.status is PlanStatus.BLOCKED:
            return PlanExecution(plan, ExecutionStatus.BLOCKED, warnings=list(plan.warnings))
        if plan.status is PlanStatus.PLANNING_REQUIRED:
            return PlanExecution(
                plan,
                ExecutionStatus.DEFERRED,
                warnings=[
                    *list(plan.warnings),
                    "Analysis plan has not been compiled by the governed Analysis Planner yet.",
                ],
            )
        if plan.status not in {PlanStatus.PLANNED, PlanStatus.NEEDS_DISCOVERY}:
            return PlanExecution(plan, ExecutionStatus.STOPPED, warnings=list(plan.warnings))

        results: list[dict[str, Any]] = []
        final = ExecutionStatus.NEEDS_DISCOVERY if plan.status is PlanStatus.NEEDS_DISCOVERY else ExecutionStatus.COMPLETE
        for step in plan.steps:
            if step.tool == "fetch_top_knowledge_hits":
                fetched = self._execute_exact_knowledge_fetches(results, **step.arguments)
                results.extend(fetched)
                if fetched:
                    final = self._map_status(fetched[-1].get("status", "ERROR"), needs_discovery=False)
                elif plan.intent is Intent.KNOWLEDGE_QUERY:
                    final = ExecutionStatus.STOPPED
                if final in {ExecutionStatus.ERROR, ExecutionStatus.BLOCKED, ExecutionStatus.DEFERRED}:
                    break
                continue

            if step.tool in {"query_semantic_metric", "query_semantic_metrics"}:
                planner = GovernedSemanticQueryPlanner(self.root)
                if step.tool == "query_semantic_metric":
                    semantic_plan = planner.plan(
                        metric=step.arguments["metric"],
                        question=step.arguments["question"],
                        limit=step.arguments.get("limit"),
                    )
                else:
                    semantic_plan = planner.plan_metrics(
                        metrics=step.arguments["metrics"],
                        question=step.arguments["question"],
                        limit=step.arguments.get("limit"),
                    )
                result = MetricFlowSemanticQueryExecutor(self.root).execute(semantic_plan)
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
                item = self._knowledge().search_knowledge(**step.arguments)
            elif hasattr(self.tools, step.tool):
                item = getattr(self.tools, step.tool)(**step.arguments)
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
            final = self._map_status(status, needs_discovery=plan.status is PlanStatus.NEEDS_DISCOVERY)
            if status in step.stop_on_status:
                break

        if plan.status is PlanStatus.NEEDS_DISCOVERY and final is ExecutionStatus.COMPLETE:
            final = ExecutionStatus.NEEDS_DISCOVERY
        return PlanExecution(plan, final, results, warnings=[])
