from __future__ import annotations

import json

from agent.semantic_query import GovernedSemanticQueryPlanner, MetricFlowSemanticQueryExecutor
from agent.semantic_query.contracts import SemanticQueryStatus
from agent.semantic_runtime import SemanticRuntimeGuard
from infra.runtime.phase7.runtime_helpers import ROOT, require_gate, write_verified_evidence


def main() -> int:
    require_gate("PHASE7A_ALLOW_AGENT_SEMANTIC_RUNTIME")
    readiness = SemanticRuntimeGuard(ROOT).check()
    if not readiness.ready or readiness.evidence != "RUNTIME_VERIFIED":
        raise SystemExit(f"DEFERRED: semantic runtime guard not ready: {readiness.reason}")
    planner = GovernedSemanticQueryPlanner(ROOT)
    plan = planner.plan(metric="gross_sales", question="2026-08-05 gross_sales 是多少？", limit=5)
    if plan.status is not SemanticQueryStatus.READY:
        raise SystemExit(f"BLOCKED: acceptance query did not produce READY plan: {plan.status.value}")
    result = MetricFlowSemanticQueryExecutor(ROOT).execute(plan)
    if result.status is not SemanticQueryStatus.COMPLETE or result.evidence != "RUNTIME_VERIFIED":
        raise SystemExit(f"DEFERRED: live MetricFlow acceptance did not complete: {result.status.value} / {result.evidence}")
    output = write_verified_evidence(
        ".runtime/evidence/phase7a/agent_semantic_runtime.json",
        status="AGENT_SEMANTIC_RUNTIME_CUTOVER_VERIFIED",
        details={
            "contract": "commerce_phase7a_agent_semantic_runtime",
            "metric": "gross_sales",
            "query_window": {"start": plan.spec.start_time, "end": plan.spec.end_time} if plan.spec else None,
            "row_count": len(result.rows),
            "validation": result.validation,
            "semantic_authority": "dbt_metricflow",
        },
    )
    print(json.dumps({"status": "AGENT_SEMANTIC_RUNTIME_CUTOVER_VERIFIED", "evidence": str(output.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
