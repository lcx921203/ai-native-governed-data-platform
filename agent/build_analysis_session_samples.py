from __future__ import annotations
import json
from pathlib import Path
from agent.analysis_session import GovernedAnalysisSession
from agent.semantic_query import GovernedSemanticQueryPlanner
ROOT=Path(__file__).resolve().parents[1]
q='2026-08-01 到 2026-08-05 按天看 gross_sales'
planner=GovernedSemanticQueryPlanner(ROOT); mgr=GovernedAnalysisSession(ROOT)
initial=planner.plan(metric='gross_sales',question=q); s1=mgr.start(initial)
r2=mgr.apply_follow_up(s1,question='那只看 West 呢？')
r3=mgr.apply_follow_up(r2.state,question='那再加上 AOV')
payload={'contract':'commerce_phase5f_analysis_session_samples','initial_plan':initial.to_dict(),'session_start':s1.to_dict(),'turns':[r2.to_dict(),r3.to_dict()]}
out=ROOT/'agent/generated/analysis_session_samples.json'; out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
print(out)
