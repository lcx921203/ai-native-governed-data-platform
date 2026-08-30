#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING=false
python infra/runtime/sync_phase5_contracts.py --repair
python -m pytest -q \
  tests/test_phase6e_incident_response_planner.py \
  tests/test_phase6d_operational_incident_drilldown.py \
  tests/test_phase6c_governed_diagnostic_orchestrator.py \
  tests/test_phase6_closure_contract.py \
  tests/test_phase5_closure_contract.py
python agent/build_incident_response_samples.py >/dev/null
python -m compileall -q agent/incident_response agent/incident_drilldown agent/diagnostic agent/response
printf 'Phase 6E incident-response planning static contract: PASS\n'
printf 'NOTE: planner is advisory-only; real Dagster recovery/backfill execution remains outside Agent authority.\n'
