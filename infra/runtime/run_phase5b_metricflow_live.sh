#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5B_ALLOW_METRICFLOW_QUERY=true explicitly before live Phase 5B semantic querying." >&2
  exit 2
fi
echo "Phase 5B live semantic-query gate accepted."
