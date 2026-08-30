#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PHASE6D_ALLOW_INCIDENT_DRILLDOWN=false
python infra/runtime/sync_phase5_contracts.py --repair
python -m pytest -q \
  tests/test_phase6d_operational_incident_drilldown.py \
  tests/test_phase6c_governed_diagnostic_orchestrator.py \
  tests/test_phase6_closure_contract.py \
  tests/test_phase5_closure_contract.py
python agent/build_incident_drilldown_samples.py >/dev/null
python -m compileall -q agent/incident_drilldown agent/diagnostic
printf 'Phase 6D operational-incident static contract: PASS\n'
printf 'NOTE: real Dagster Run Storage / recovery evidence remains DEFERRED.\n'
