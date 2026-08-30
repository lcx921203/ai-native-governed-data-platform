#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5H_ALLOW_BREAKDOWN_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5H_ALLOW_BREAKDOWN_QUERY=true explicitly before live Phase 5H breakdown execution." >&2
  exit 2
fi
if [[ "${PHASE5F_ALLOW_SESSION_EXECUTION:-false}" != "true" ]]; then
  echo "REFUSED: Phase 5H session analysis also requires PHASE5F_ALLOW_SESSION_EXECUTION=true." >&2
  exit 2
fi
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 5H live execution also requires PHASE5B_ALLOW_METRICFLOW_QUERY=true." >&2
  exit 2
fi
echo "Phase 5H live gates accepted. Execute the grouped analysis session in the intended workstation Runtime."
