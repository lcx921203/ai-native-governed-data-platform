#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE7A_ALLOW_AGENT_SEMANTIC_RUNTIME:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7A_ALLOW_AGENT_SEMANTIC_RUNTIME=true explicitly." >&2
  exit 2
fi
export PHASE5B_ALLOW_METRICFLOW_QUERY="${PHASE5B_ALLOW_METRICFLOW_QUERY:-true}"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" python infra/runtime/phase7/agent_semantic_acceptance.py
