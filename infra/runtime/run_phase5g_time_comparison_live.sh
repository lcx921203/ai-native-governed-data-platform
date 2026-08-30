#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE5G_ALLOW_COMPARATIVE_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE5G_ALLOW_COMPARATIVE_QUERY=true explicitly before live Phase 5G comparison execution." >&2
  exit 2
fi
if [[ "${PHASE5F_ALLOW_SESSION_EXECUTION:-false}" != "true" ]]; then
  echo "REFUSED: Phase 5G session comparison also requires PHASE5F_ALLOW_SESSION_EXECUTION=true." >&2
  exit 2
fi
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 5G live execution also requires PHASE5B_ALLOW_METRICFLOW_QUERY=true." >&2
  exit 2
fi
echo "Phase 5G live gates accepted. Use agent/session_cli.py follow-up --execute in the intended workstation runtime."
