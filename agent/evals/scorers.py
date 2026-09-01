"""Agent Eval 的确定性 Scorers（评分器）。"""

from __future__ import annotations

from typing import Any

from agent.context import ContextSource

from .contracts import AgentEvalCase, EvalCheck


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _check(name: str, expected: Any, actual: Any, *, message: str = "") -> EvalCheck:
    return EvalCheck(
        name=name,
        passed=actual == expected,
        expected=expected,
        actual=actual,
        message=message,
    )


def score_route(case: AgentEvalCase, route: Any) -> list[EvalCheck]:
    expect = case.expect
    checks: list[EvalCheck] = []

    mapping = {
        "intent": _enum_value(getattr(route, "intent", "")),
        "route_status": _enum_value(getattr(route, "status", "")),
        "target_kind": getattr(route, "target_kind", None),
        "target_id": getattr(route, "target_id", None),
    }
    for key, actual in mapping.items():
        if key in expect:
            checks.append(_check(key, expect[key], actual))

    if "tool_names" in expect:
        actual_tools = [str(step.tool) for step in getattr(route, "steps", ()) or ()]
        checks.append(_check("tool_names", list(expect["tool_names"]), actual_tools))

    if "max_tool_calls" in expect:
        actual_count = len(getattr(route, "steps", ()) or ())
        expected_max = int(expect["max_tool_calls"])
        checks.append(
            EvalCheck(
                "max_tool_calls",
                actual_count <= expected_max,
                f"<= {expected_max}",
                actual_count,
            )
        )

    if "must_be_blocked" in expect:
        actual = _enum_value(getattr(route, "status", "")) == "BLOCKED"
        checks.append(_check("must_be_blocked", bool(expect["must_be_blocked"]), actual))

    return checks


def score_context(case: AgentEvalCase, context_plan: Any | None) -> list[EvalCheck]:
    expect = case.expect
    checks: list[EvalCheck] = []
    if not any(
        key in expect
        for key in (
            "required_context",
            "optional_context",
            "forbidden_context",
        )
    ):
        return checks

    if context_plan is None:
        return [
            EvalCheck(
                "context_plan_present",
                False,
                "ContextPlan",
                None,
                "Case expected ContextPlan checks but no plan was produced.",
            )
        ]

    required = [source.value for source in context_plan.required_sources()]
    optional = [source.value for source in context_plan.optional_sources()]
    all_sources = set(required) | set(optional)

    if "required_context" in expect:
        checks.append(
            _check(
                "required_context",
                list(expect["required_context"]),
                required,
            )
        )
    if "optional_context" in expect:
        checks.append(
            _check(
                "optional_context",
                list(expect["optional_context"]),
                optional,
            )
        )
    if "forbidden_context" in expect:
        forbidden = set(str(x) for x in expect["forbidden_context"])
        actual_forbidden_loaded = sorted(forbidden & all_sources)
        checks.append(
            _check(
                "forbidden_context",
                [],
                actual_forbidden_loaded,
                message="These Context sources must not be requested by this route.",
            )
        )

    return checks


def score_analysis(case: AgentEvalCase, analysis_plan: Any | None) -> list[EvalCheck]:
    expect = case.expect
    analysis_keys = {
        "analysis_status",
        "skill_id",
        "comparison_mode",
        "unit_kinds",
        "unit_count",
        "required_unit_count",
    }
    if not any(key in expect for key in analysis_keys):
        return []

    if analysis_plan is None:
        return [
            EvalCheck(
                "analysis_plan_present",
                False,
                "AnalysisPlan",
                None,
                "Case expected AnalysisPlan checks but no plan was produced.",
            )
        ]

    checks: list[EvalCheck] = []
    if "analysis_status" in expect:
        checks.append(
            _check(
                "analysis_status",
                expect["analysis_status"],
                _enum_value(analysis_plan.status),
            )
        )
    if "skill_id" in expect:
        checks.append(_check("skill_id", expect["skill_id"], analysis_plan.skill_id))
    if "comparison_mode" in expect:
        actual_mode = (
            _enum_value(analysis_plan.comparison.mode)
            if analysis_plan.comparison is not None
            else None
        )
        checks.append(_check("comparison_mode", expect["comparison_mode"], actual_mode))
    if "unit_kinds" in expect:
        actual = [_enum_value(unit.kind) for unit in analysis_plan.units]
        checks.append(_check("unit_kinds", list(expect["unit_kinds"]), actual))
    if "unit_count" in expect:
        checks.append(_check("unit_count", int(expect["unit_count"]), len(analysis_plan.units)))
    if "required_unit_count" in expect:
        actual = sum(1 for unit in analysis_plan.units if unit.required)
        checks.append(
            _check(
                "required_unit_count",
                int(expect["required_unit_count"]),
                actual,
            )
        )

    return checks
