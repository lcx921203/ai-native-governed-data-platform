from __future__ import annotations
import argparse, json
from pathlib import Path
from agent.semantic_query import GovernedSemanticQueryPlanner, MetricFlowSemanticQueryExecutor

def main():
    p=argparse.ArgumentParser(description='Governed MetricFlow semantic-query CLI'); p.add_argument('question'); p.add_argument('--metrics',required=True); p.add_argument('--execute',action='store_true'); args=p.parse_args()
    root=Path(__file__).resolve().parents[1]; metrics=[x.strip() for x in args.metrics.split(',') if x.strip()]; planner=GovernedSemanticQueryPlanner(root)
    plan=planner.plan_metrics(metrics=metrics,question=args.question)
    payload=MetricFlowSemanticQueryExecutor(root).execute(plan).to_dict() if args.execute else plan.to_dict()
    print(json.dumps(payload,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
