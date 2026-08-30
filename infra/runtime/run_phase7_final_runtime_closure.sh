#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ "${PHASE7_ALLOW_FINAL_RUNTIME_CLOSURE:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7_ALLOW_FINAL_RUNTIME_CLOSURE=true explicitly." >&2
  exit 2
fi

# 先重新跑静态闭包，避免在过期源码上做最终 Runtime 认证。
bash infra/runtime/run_phase6_static_closure.sh
# Phase 7A：依次收集 Core / DataHub / Agent Metadata / Semantic / Dagster / OpenAI 的真实证据。
PHASE7A_ALLOW_RUNTIME_BOOTSTRAP=true bash infra/runtime/run_phase7a_core_bootstrap.sh
PHASE7A_ALLOW_DATAHUB_BOOTSTRAP=true bash infra/runtime/run_phase7a_datahub_bootstrap.sh
PHASE7A_ALLOW_DATAHUB_GOVERNANCE_WRITE=true bash infra/runtime/run_phase7a_datahub_acceptance.sh
PHASE7A_ALLOW_AGENT_DATAHUB_READ=true bash infra/runtime/run_phase7a_agent_metadata_live.sh
PHASE7A_ALLOW_AGENT_SEMANTIC_RUNTIME=true bash infra/runtime/run_phase7a_agent_semantic_live.sh
PHASE7A_ALLOW_AGENT_DAGSTER_READ=true bash infra/runtime/run_phase7a_dagster_operational_live.sh
# Phase 7B：真实重建知识索引、检索、Rerank，并验证 Hybrid RAG Agent。
PHASE7B_ALLOW_KNOWLEDGE_REINDEX=true bash infra/runtime/run_phase7b_rag_live.sh
PHASE7B_ALLOW_KNOWLEDGE_RETRIEVAL=true PHASE7B_ALLOW_KNOWLEDGE_RERANK=true bash infra/runtime/run_phase7b_retrieval_live.sh
bash infra/runtime/run_phase7b_hybrid_agent_live.sh
# Phase 7C：通过 OAuth-protected Streamable HTTP 做 MCP Runtime Acceptance。
bash infra/runtime/run_phase7c_mcp_acceptance.sh
PHASE7A_ALLOW_OPENAI_PROVIDER=true bash infra/runtime/run_phase7a_openai_agent_live.sh
# Serving / Consumption：固定消费链也必须真实跑通，不能因为 Agent Runtime 已验证就跳过 BI/API。
if [[ -z "${SERVING_ACCEPTANCE_PARTITION_KEY:-}" ]]; then
  echo "DEFERRED: set SERVING_ACCEPTANCE_PARTITION_KEY to a business date with prepared upstream data." >&2
  exit 3
fi
SERVING_ALLOW_RUNTIME_ACCEPTANCE=true bash infra/runtime/run_serving_runtime.sh "$SERVING_ACCEPTANCE_PARTITION_KEY"
bash infra/runtime/run_serving_governance_runtime.sh

# 最终聚合 Contract 中全部 13 份 evidence；缺任何一份都不能输出 END_TO_END VERIFIED。
python infra/runtime/phase7/collect_phase7_final_evidence.py
