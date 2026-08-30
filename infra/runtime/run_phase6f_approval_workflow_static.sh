#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PHASE6F_ALLOW_APPROVAL_WORKFLOW=false
export PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE=false
python infra/runtime/sync_phase5_contracts.py --repair
python -m pytest -q \
  tests/test_phase6f_approval_workflow.py \
  tests/test_phase6e_incident_response_planner.py \
  tests/test_phase6d_operational_incident_drilldown.py \
  tests/test_phase6_closure_contract.py \
  tests/test_phase5_closure_contract.py
python agent/build_approval_workflow_samples.py >/dev/null
python -m compileall -q agent/approval_workflow agent/incident_response agent/response
printf 'Phase 6F approval workflow static contract: PASS\n'
printf 'NOTE: approval records are governance state only; APPROVED is not EXECUTED and Agent production action authority remains false.\n'
