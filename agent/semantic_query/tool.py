from __future__ import annotations
from pathlib import Path
from .planner import GovernedSemanticQueryPlanner
from .executor import MetricFlowSemanticQueryExecutor

def query_semantic_metrics(project_root:Path|str,*,metrics,question,limit=20):
    root=Path(project_root); planner=GovernedSemanticQueryPlanner(root); plan=planner.plan_metrics(metrics=metrics,question=question,limit=limit); return MetricFlowSemanticQueryExecutor(root).execute(plan)

def query_semantic_metric(project_root:Path|str,*,metric,question,limit=20):
    return query_semantic_metrics(project_root,metrics=[metric],question=question,limit=limit)
