#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5D_ALLOW_DIMENSION_RESOLUTION:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5D_ALLOW_DIMENSION_RESOLUTION=true explicitly before live Phase 5D value resolution." >&2
  exit 2
fi
if [[ "${PHASE5C_ALLOW_METRICFLOW_DISCOVERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 5D dynamic resolution also requires PHASE5C_ALLOW_METRICFLOW_DISCOVERY=true." >&2
  exit 2
fi
echo "Phase 5D live resolution gates accepted."
