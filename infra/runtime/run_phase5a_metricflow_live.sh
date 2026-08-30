#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5B_ALLOW_METRICFLOW_QUERY=true explicitly before live semantic querying." >&2
  exit 2
fi
echo "Phase 5A live query gate accepted. Execute agent/query_cli.py in the intended workstation runtime."
