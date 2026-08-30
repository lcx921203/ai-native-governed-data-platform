#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE6F_ALLOW_APPROVAL_WORKFLOW:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE6F_ALLOW_APPROVAL_WORKFLOW=true explicitly before live Phase 6F approval workflow." >&2
  exit 2
fi
if [[ "${PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING:-false}" != "true" ]]; then
  echo "REFUSED: Phase 6F requires PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING=true for governed response-plan evidence." >&2
  exit 2
fi
echo "Phase 6F workflow gate accepted. This enables approval-state processing only; it does not enable recovery/backfill execution."
echo "Production approval identity must come from an authenticated upstream service. Audit persistence additionally requires PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE=true."
