from __future__ import annotations
import json
from pathlib import Path
from agent.router import DeterministicToolRouter, GovernedPlanExecutor

def main():
    root=Path(__file__).resolve().parents[1]; router=DeterministicToolRouter(root); executor=GovernedPlanExecutor(root)
    questions={
      'metric_definition':'activity_net_sales 是什么意思？',
      'entity_context':'订单实体是什么？',
      'dataset_governance':'orders 属于哪个业务域，谁负责？',
      'lineage_upstream':'orders 的上游血缘是什么？',
      'runtime_diagnosis':'为什么 orders 昨天没更新？',
      'unknown_metric':'refund_rate 这个指标怎么算？',
      'blocked_sql':'执行 SQL: select * from orders',
      'dimension_values':'gross_sales 有哪些地区可以筛？',
      'knowledge_design':'为什么这样设计：MySQL CDC 不先经过 Kafka？',
    }
    samples={}
    for name,q in questions.items():
        plan=router.plan(q); execution=executor.execute(plan); samples[name]={'question':q,'plan':plan.to_dict(),'execution':execution.to_dict()}
    path=root/'agent/generated/routing_samples.json'; path.write_text(json.dumps({'contract':'commerce_agent_routing_samples','samples':samples},ensure_ascii=False,indent=2)+'\n'); print(path)
if __name__=='__main__': main()
