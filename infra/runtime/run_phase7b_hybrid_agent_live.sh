#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
python - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
from agent.knowledge.hybrid import ClaimAuthorityMatrix
root=Path.cwd()
required={
 'semantic':root/'.runtime/evidence/phase7a/agent_semantic_runtime.json',
 'metadata':root/'.runtime/evidence/phase7a/agent_metadata_runtime.json',
 'operational':root/'.runtime/evidence/phase7a/dagster_operational_runtime.json',
 'knowledge':root/'.runtime/evidence/phase7b/knowledge_retrieval.json',
 'reranker':root/'.runtime/evidence/phase7b/knowledge_reranker.json',
}
checks={}; ok=True
for name,path in required.items():
    try: p=json.loads(path.read_text()); passed=p.get('runtime_verified') is True
    except Exception: p={}; passed=False
    checks[name]={'passed':passed,'source':str(path.relative_to(root)),'status':p.get('status')}; ok &= passed
matrix=ClaimAuthorityMatrix(root)
ok &= matrix.decide('runtime_status',[{'source':'knowledge_rag','value':'healthy'}]).accepted is False
payload={'contract':'commerce_phase7b_hybrid_runtime','generated_at':datetime.now(timezone.utc).isoformat(),'runtime_verified':bool(ok),'status':'HYBRID_RAG_AGENT_RUNTIME_VERIFIED' if ok else 'HYBRID_RAG_AGENT_RUNTIME_DEFERRED','components':checks,'authority_conflict_guard':True}
out=root/'.runtime/evidence/phase7b/hybrid_rag_agent.json'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+'\n'); print(json.dumps(payload,indent=2,ensure_ascii=False))
raise SystemExit(0 if ok else 1)
PY
