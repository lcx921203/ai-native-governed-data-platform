#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING=true explicitly before live Phase 6E incident-response planning." >&2
  exit 2
fi
if [[ "${PHASE6D_ALLOW_INCIDENT_DRILLDOWN:-false}" != "true" ]]; then
  echo "REFUSED: Phase 6E requires PHASE6D_ALLOW_INCIDENT_DRILLDOWN=true for runtime-verified structured incident evidence." >&2
  exit 2
fi
if [[ "${PHASE6C_ALLOW_DIAGNOSTIC:-false}" != "true" ]]; then
  echo "REFUSED: Phase 6E diagnostic-chain runtime also requires PHASE6C_ALLOW_DIAGNOSTIC=true." >&2
  exit 2
fi
echo "Phase 6E live planning gate accepted. This capability is advisory-only and has no Dagster recovery/backfill write authority."
echo "Use the governed diagnostic CLI inside the intended runtime environment; execution remains owned by Phase 3C automation or a human operator."
