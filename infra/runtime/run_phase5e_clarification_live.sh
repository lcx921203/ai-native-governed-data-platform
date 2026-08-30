#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5E_ALLOW_CONTINUATION_EXECUTION:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5E_ALLOW_CONTINUATION_EXECUTION=true explicitly before live Phase 5E continuation." >&2
  exit 2
fi
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 5E live continuation also requires PHASE5B_ALLOW_METRICFLOW_QUERY=true." >&2
  exit 2
fi
echo "Phase 5E live continuation gates accepted."
