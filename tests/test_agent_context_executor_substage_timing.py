"""Context Loader / Executor 子阶段耗时契约测试。"""

from __future__ import annotations

from pathlib import Path

from agent.context import GovernedContextLoader, GovernedContextPlanner
from agent.router import DeterministicToolRouter, GovernedPlanExecutor


ROOT = Path(__file__).resolve().parents[1]
QUESTION = "activity_net_sales 是什么意思？"


def test_metric_definition_context_loader_records_internal_substages():
    """真实 Metric Definition Loader 必须区分 Repository Lookup 与 Token Estimate。"""

    route = DeterministicToolRouter(ROOT).plan(QUESTION)
    context_plan = GovernedContextPlanner(ROOT).plan(route)
    bundle = GovernedContextLoader(ROOT).load(
        route,
        context_plan,
    )

    timings = dict(bundle.substage_timings)

    assert timings
    assert timings["route_binding"] >= 0.0
    assert timings["semantic.target_resolution"] >= 0.0
    assert timings["semantic.repository_lookup"] >= 0.0
    assert timings["semantic.token_estimate"] >= 0.0
    assert timings["finalize"] >= 0.0

    # Timing 是内部性能证据，不进入业务 Context Bundle 序列化。
    assert "substage_timings" not in bundle.to_dict()


def test_metric_definition_executor_records_exact_governed_tool_substage():
    """METRIC_DEFINITION 的 get_metric_context Tool 必须有独立执行耗时。"""

    route = DeterministicToolRouter(ROOT).plan(QUESTION)
    execution = GovernedPlanExecutor(ROOT).execute(route)

    timings = dict(execution.substage_timings)

    assert timings["preflight"] >= 0.0
    assert timings["tool.get_metric_context.execute"] >= 0.0
    assert timings["status_mapping"] >= 0.0

    # Tool 内部性能结构不能被 PlanExecution.to_dict() 当成公共 Payload 暴露。
    assert "substage_timings" not in execution.to_dict()



def test_executor_timing_label_rejects_unbounded_or_non_ascii_tool_name():
    """Audit Timing Label 只能来自 bounded ASCII Tool Name。"""

    assert GovernedPlanExecutor._tool_timing_label("get_metric_context") == "get_metric_context"
    assert GovernedPlanExecutor._tool_timing_label("tool.with.dots") == "unknown"
    assert GovernedPlanExecutor._tool_timing_label("包含用户文本") == "unknown"
