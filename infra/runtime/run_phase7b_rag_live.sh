#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
# Runtime Gate：只有显式允许 reindex 时才允许调用真实 Embedding + Qdrant。
if [[ "${PHASE7B_ALLOW_KNOWLEDGE_REINDEX:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7B_ALLOW_KNOWLEDGE_REINDEX=true explicitly." >&2
  exit 2
fi
# Python 入口执行 Corpus → Chunk → Embedding → Qdrant，并在成功回查 point count 后写 Runtime Evidence。
python - <<'PY'
from pathlib import Path
from agent.knowledge.indexer import KnowledgeIndexer, write_runtime_evidence
root=Path.cwd()
payload=KnowledgeIndexer(root).index(require_runtime_gate=True)
out=write_runtime_evidence(root,payload)
print(out)
PY
