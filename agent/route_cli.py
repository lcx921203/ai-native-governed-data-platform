from __future__ import annotations
import argparse, json
from pathlib import Path
from agent.router import DeterministicToolRouter, GovernedPlanExecutor

def main():
    p=argparse.ArgumentParser(description='Governed deterministic Agent router CLI'); p.add_argument('question'); p.add_argument('--execute',action='store_true'); args=p.parse_args()
    root=Path(__file__).resolve().parents[1]; plan=DeterministicToolRouter(root).plan(args.question)
    payload=GovernedPlanExecutor(root).execute(plan).to_dict() if args.execute else plan.to_dict()
    print(json.dumps(payload,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
