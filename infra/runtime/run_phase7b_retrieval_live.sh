#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# Runtime Gate：真实检索前必须显式允许，并且 Retriever 还会二次校验已验证的索引 evidence。
if [[ "${PHASE7B_ALLOW_KNOWLEDGE_RETRIEVAL:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7B_ALLOW_KNOWLEDGE_RETRIEVAL=true explicitly." >&2
  exit 2
fi
# 对人工标注的 Golden Cases 计算 Recall/MRR/NDCG 与延迟；Reranker 没真正运行则整个 live acceptance 失败。
python - <<'PY'
import json, time
from datetime import datetime, timezone
from pathlib import Path
import yaml
from agent.knowledge.retrieval import GovernedKnowledgeRetriever
from agent.knowledge.evaluation import recall_at_k, reciprocal_rank, ndcg_at_k, percentile
root=Path.cwd(); cases=yaml.safe_load((root/'metadata/knowledge/retrieval_eval_cases.yml').read_text())['cases']
retriever=GovernedKnowledgeRetriever(root)
lat=[]; rows=[]; reranked_seen=False
for case in cases:
    start=time.perf_counter()
    hits=retriever.search(case['query'], scopes=case.get('filters',{}).get('scopes'), top_k=5)
    lat.append((time.perf_counter()-start)*1000)
    ids=[h.document_id for h in hits]
    relevance={x['document_id']:int(x['relevance']) for x in case['relevant']}
    reranked_seen = reranked_seen or any(h.rerank_rank is not None for h in hits)
    rows.append({'id':case['id'],'documents':ids,'recall_at_5':recall_at_k(ids,set(relevance),k=5),'mrr_at_5':reciprocal_rank(ids,set(relevance),k=5),'ndcg_at_5':ndcg_at_k(ids,relevance,k=5)})
base={'generated_at':datetime.now(timezone.utc).isoformat(),'runtime_verified':True,'cases':rows,'latency_ms':{'p50':percentile(lat,.5),'p95':percentile(lat,.95)}}
out=root/'.runtime/evidence/phase7b'; out.mkdir(parents=True,exist_ok=True)
retrieval={**base,'contract':'commerce_phase7b_retrieval_runtime','status':'KNOWLEDGE_RETRIEVAL_RUNTIME_VERIFIED'}
(out/'knowledge_retrieval.json').write_text(json.dumps(retrieval,indent=2,ensure_ascii=False)+'\n')
rerank={**base,'contract':'commerce_phase7b_reranker_runtime','runtime_verified':bool(reranked_seen),'status':'KNOWLEDGE_RERANKER_RUNTIME_VERIFIED' if reranked_seen else 'KNOWLEDGE_RERANKER_RUNTIME_DEFERRED'}
(out/'knowledge_reranker.json').write_text(json.dumps(rerank,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'retrieval':retrieval['status'],'reranker':rerank['status']},indent=2))
if not reranked_seen:
    raise SystemExit('Reranker runtime was not verified; set PHASE7B_ALLOW_KNOWLEDGE_RERANK=true and configure provider.')
PY
