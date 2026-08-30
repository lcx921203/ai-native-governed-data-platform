from __future__ import annotations
import json
from pathlib import Path
from agent.router import DeterministicToolRouter, GovernedPlanExecutor
from agent.response import GovernedResponseComposer
from agent.semantic_query import GovernedSemanticQueryPlanner, MetricFlowSemanticQueryExecutor

def main():
    root=Path(__file__).resolve().parents[1]
    planner=GovernedSemanticQueryPlanner(root); executor=MetricFlowSemanticQueryExecutor(root)
    router=DeterministicToolRouter(root); plan_executor=GovernedPlanExecutor(root); composer=GovernedResponseComposer(root)

    single=planner.plan(metric='activity_net_sales',question='2026-08-05 activity_net_sales 是多少？')
    missing=planner.plan(metric='gross_sales',question='gross_sales 是多少？')
    question='2026-08-01 到 2026-08-05 美国西部地区，按天看 毛销售额、活动净销售额和客单价'
    route=router.plan(question); execution=plan_executor.execute(route); envelope=composer.compose(execution)

    samples={
      'single':{'plan':single.to_dict(),'execution':executor.execute(single).to_dict()},
      'missing_time':{'plan':missing.to_dict(),'execution':executor.execute(missing).to_dict()},
      'filtered_multi_metric':{
          'question':question,
          'route':route.to_dict(),
          'execution':execution.to_dict(),
          'envelope':envelope.to_dict(),
      },
    }
    path=root/'agent/generated/semantic_query_samples.json'; path.write_text(json.dumps({'contract':'commerce_semantic_query_samples','samples':samples},ensure_ascii=False,indent=2)+'\n'); print(path)
if __name__=='__main__': main()
