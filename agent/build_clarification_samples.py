from __future__ import annotations
import json
from pathlib import Path
from agent.clarification import GovernedClarificationContinuation
from agent.semantic_query import GovernedSemanticQueryPlanner

def main():
    root=Path(__file__).resolve().parents[1]; planner=GovernedSemanticQueryPlanner(root); manager=GovernedClarificationContinuation(root)
    plan=planner.plan(metric='gross_sales',question='2026-08-05 美国 品牌为 Coca Colaa 的 gross_sales 是多少？')
    state=manager.prepare(plan); confirmed=manager.resume(state,user_reply='对',execute=False); rejected=manager.resume(state,user_reply='不是',execute=False)
    payload={'contract':'commerce_clarification_samples','start':state.to_dict(),'confirmed':confirmed.to_dict(),'rejected':rejected.to_dict()}
    path=root/'agent/generated/clarification_samples.json'; path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n'); print(path)
if __name__=='__main__': main()
