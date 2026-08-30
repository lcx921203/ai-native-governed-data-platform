from __future__ import annotations

import json
from pathlib import Path

from agent.analysis_session import GovernedAnalysisSession
from agent.semantic_query import GovernedSemanticQueryPlanner

ROOT = Path(__file__).resolve().parents[1]
QUESTION = "2026-08-01 到 2026-08-05 按天看 gross_sales"

planner = GovernedSemanticQueryPlanner(ROOT)
manager = GovernedAnalysisSession(ROOT)
initial = planner.plan(metric="gross_sales", question=QUESTION)
s1 = manager.start(initial)
r2 = manager.apply_follow_up(s1, question="那只看 West 呢？")
r3 = manager.apply_follow_up(r2.state, question="那再加上 AOV")
r4 = manager.apply_follow_up(r3.state, question="和前5天比呢？")
r5 = manager.apply_follow_up(r4.state, question="增长了多少？")
r6 = manager.apply_follow_up(r5.state, question="同比呢？")

payload = {
    "contract": "commerce_phase5g_time_comparison_samples",
    "initial_plan": initial.to_dict(),
    "session_start": s1.to_dict(),
    "turns": [r2.to_dict(), r3.to_dict(), r4.to_dict(), r5.to_dict(), r6.to_dict()],
}
out = ROOT / "agent/generated/time_comparison_samples.json"
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(out)
