from __future__ import annotations
import argparse, json
from pathlib import Path
from agent.clarification import GovernedClarificationContinuation
from agent.semantic_query import GovernedSemanticQueryPlanner

def main():
    p=argparse.ArgumentParser(description='Governed clarification continuation CLI'); sub=p.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('start'); s.add_argument('question'); s.add_argument('--metrics',required=True); s.add_argument('--state',required=True)
    r=sub.add_parser('resume'); r.add_argument('--state',required=True); r.add_argument('--reply',required=True); r.add_argument('--execute',action='store_true')
    args=p.parse_args(); root=Path(__file__).resolve().parents[1]; mgr=GovernedClarificationContinuation(root); state_path=Path(args.state)
    if args.cmd=='start':
        metrics=[x.strip() for x in args.metrics.split(',') if x.strip()]; plan=GovernedSemanticQueryPlanner(root).plan_metrics(metrics=metrics,question=args.question); state=mgr.prepare(plan); state_path.write_text(json.dumps(state.to_dict(),ensure_ascii=False,indent=2)+'\n'); print(json.dumps(state.to_dict(),ensure_ascii=False,indent=2))
    else:
        state=mgr.from_dict(json.loads(state_path.read_text())); result=mgr.resume(state,user_reply=args.reply,execute=args.execute); print(json.dumps(result.to_dict(),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
