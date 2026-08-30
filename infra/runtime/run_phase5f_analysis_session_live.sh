#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5F_ALLOW_SESSION_EXECUTION:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5F_ALLOW_SESSION_EXECUTION=true explicitly before live Phase 5F session execution." >&2
  exit 2
fi
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 5F live execution also requires PHASE5B_ALLOW_METRICFLOW_QUERY=true." >&2
  exit 2
fi
echo "Phase 5F live gates accepted. Use agent/session_cli.py follow-up --execute in the intended workstation runtime."
