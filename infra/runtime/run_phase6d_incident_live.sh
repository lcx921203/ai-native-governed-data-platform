#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE6D_ALLOW_INCIDENT_DRILLDOWN:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE6D_ALLOW_INCIDENT_DRILLDOWN=true explicitly before live Phase 6D incident drilldown." >&2
  exit 2
fi
if [[ "${PHASE6C_ALLOW_DIAGNOSTIC:-false}" != "true" ]]; then
  echo "REFUSED: Phase 6D diagnostic-chain runtime also requires PHASE6C_ALLOW_DIAGNOSTIC=true." >&2
  exit 2
fi
echo "Phase 6D live gate accepted. Run the diagnostic CLI inside the intended Dagster runtime environment."
