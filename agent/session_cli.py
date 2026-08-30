from __future__ import annotations
import argparse,json
from pathlib import Path
from agent.analysis_session import GovernedAnalysisSession
from agent.semantic_query import GovernedSemanticQueryPlanner

def main():
    ap=argparse.ArgumentParser(description='Governed analysis-session CLI')
    sub=ap.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('start'); s.add_argument('question'); s.add_argument('--metrics',required=True); s.add_argument('--state',required=True)
    f=sub.add_parser('follow-up'); f.add_argument('question'); f.add_argument('--state',required=True); f.add_argument('--execute',action='store_true')
    args=ap.parse_args(); root=Path(__file__).resolve().parents[1]; mgr=GovernedAnalysisSession(root); state_path=Path(args.state)
    if args.cmd=='start':
        metrics=[x.strip() for x in args.metrics.split(',') if x.strip()]
        plan=GovernedSemanticQueryPlanner(root).plan_metrics(metrics=metrics,question=args.question)
        state=mgr.start(plan); state_path.write_text(json.dumps(state.to_dict(),ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(state.to_dict(),ensure_ascii=False,indent=2))
    else:
        state=mgr.from_dict(json.loads(state_path.read_text()))
        result=mgr.apply_follow_up(state,question=args.question,execute=args.execute)
        state_path.write_text(json.dumps(result.state.to_dict(),ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(result.to_dict(),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
