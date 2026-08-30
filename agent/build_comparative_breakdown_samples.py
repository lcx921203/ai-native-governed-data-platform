from __future__ import annotations

import json
import os
from pathlib import Path

from agent.analysis_session import GovernedAnalysisSession
from agent.breakdown_analysis import GovernedComparativeBreakdown
from agent.semantic_query import GovernedSemanticQueryPlanner

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "2026-08-01 到 2026-08-05 按地区看 gross_sales"

planner = GovernedSemanticQueryPlanner(ROOT)
manager = GovernedAnalysisSession(ROOT)
engine = GovernedComparativeBreakdown(ROOT)

initial = planner.plan(metric="gross_sales", question=QUESTION)
s1 = manager.start(initial)
yoy = manager.apply_follow_up(s1, question="同比呢？")
ranked = manager.apply_follow_up(yoy.state, question="哪个地区增长最多？")
contribution = manager.apply_follow_up(ranked.state, question="总增长主要是谁贡献的？")

# Runtime remains intentionally disabled in generated repository samples.
os.environ.pop("PHASE5H_ALLOW_BREAKDOWN_QUERY", None)
deferred = engine.execute(contribution.breakdown_plan)

payload = {
    "contract": "commerce_phase5h_comparative_breakdown_samples",
    "evidence_boundary": "STATIC_CONTRACT_ONLY; real grouped MetricFlow execution is DEFERRED",
    "initial_plan": initial.to_dict(),
    "session_start": s1.to_dict(),
    "yoy_breakdown": yoy.to_dict(),
    "top_growth_plan": ranked.to_dict(),
    "contribution_plan": contribution.to_dict(),
    "deferred_contribution_execution": deferred.to_dict(),
}
out = ROOT / "agent/generated/comparative_breakdown_samples.json"
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(out)
