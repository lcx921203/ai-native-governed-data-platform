#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5C_ALLOW_METRICFLOW_DISCOVERY:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5C_ALLOW_METRICFLOW_DISCOVERY=true explicitly before live Phase 5C dimension-value discovery." >&2
  exit 2
fi
echo "Phase 5C live dimension-value discovery gate accepted."
