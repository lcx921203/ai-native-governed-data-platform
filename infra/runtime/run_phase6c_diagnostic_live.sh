#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

required=(
  PHASE6C_ALLOW_DIAGNOSTIC
  PHASE6B_ALLOW_DRIVER_ATTRIBUTION
  PHASE6A_ALLOW_ANOMALY_QUERY
  PHASE5B_ALLOW_METRICFLOW_QUERY
)
for gate in "${required[@]}"; do
  if [[ "${!gate:-false}" != "true" ]]; then
    echo "REFUSED: set ${gate}=true explicitly before live Phase 6C diagnostic execution." >&2
    exit 2
  fi
done

question="${PHASE6C_DIAGNOSTIC_QUESTION:-为什么 2026-08-05 Gross Sales 跌了这么多？}"
python agent/diagnostic_cli.py "$question"
