#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
python -m pytest -q tests/test_phase7b_knowledge_rag.py tests/test_phase7b_hybrid_authority.py
python -m py_compile agent/knowledge/chunking.py agent/knowledge/qdrant_store.py agent/knowledge/indexer.py agent/knowledge/retrieval.py agent/knowledge/tools.py agent/knowledge/evaluation.py agent/knowledge/hybrid.py
echo "Phase 7B static source closure: PASS"
echo "Runtime evidence: DEFERRED"
