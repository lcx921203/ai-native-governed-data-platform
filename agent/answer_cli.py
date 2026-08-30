from __future__ import annotations
import argparse, json
from pathlib import Path
from agent.router import DeterministicToolRouter, GovernedPlanExecutor
from agent.response import GovernedResponseComposer, render_deterministic, validate_answer_draft

def main():
    p=argparse.ArgumentParser(description='Governed evidence-first answer CLI'); p.add_argument('question'); p.add_argument('--json',action='store_true'); args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    plan=DeterministicToolRouter(root).plan(args.question); execution=GovernedPlanExecutor(root).execute(plan)
    envelope=GovernedResponseComposer(root).compose(execution); draft=render_deterministic(envelope); validate_answer_draft(envelope,draft)
    if args.json:
        print(json.dumps({'envelope':envelope.to_dict(),'draft':{'answer':draft.answer,'used_claim_ids':list(draft.used_claim_ids),'acknowledged_limitations':list(draft.acknowledged_limitations)}},ensure_ascii=False,indent=2))
    else: print(draft.answer)
if __name__=='__main__': main()
