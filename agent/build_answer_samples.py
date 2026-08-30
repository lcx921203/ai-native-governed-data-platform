from __future__ import annotations
import json
from pathlib import Path
from agent.router import DeterministicToolRouter, GovernedPlanExecutor
from agent.response import GovernedResponseComposer, render_deterministic, validate_answer_draft

def main():
    root=Path(__file__).resolve().parents[1]; router=DeterministicToolRouter(root); executor=GovernedPlanExecutor(root); composer=GovernedResponseComposer(root)
    questions={
      'metric_definition':'activity_net_sales 是什么意思？',
      'runtime_diagnosis':'为什么 orders 昨天没更新？',
      'metric_query':'2026-08-05 activity_net_sales 是多少？',
      'unknown_metric':'refund_rate 这个指标怎么算？',
      'knowledge_design':'为什么这样设计：MySQL CDC 不先经过 Kafka？',
    }
    samples={}
    for name,q in questions.items():
        execution=executor.execute(router.plan(q)); envelope=composer.compose(execution); draft=render_deterministic(envelope); validate_answer_draft(envelope,draft)
        samples[name]={'question':q,'envelope':envelope.to_dict(),'draft':{'answer':draft.answer,'used_claim_ids':list(draft.used_claim_ids),'acknowledged_limitations':list(draft.acknowledged_limitations)}}
    path=root/'agent/generated/answer_samples.json'; path.write_text(json.dumps({'contract':'commerce_agent_answer_samples','samples':samples},ensure_ascii=False,indent=2)+'\n'); print(path)
if __name__=='__main__': main()
