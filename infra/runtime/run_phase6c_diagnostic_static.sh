#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
python infra/runtime/sync_phase5_contracts.py --repair
python -m pytest -q \
  tests/test_phase6a_governed_anomaly_detection.py \
  tests/test_phase6b_governed_driver_attribution.py \
  tests/test_phase6c_governed_diagnostic_orchestrator.py \
  tests/test_phase6_closure_contract.py
python agent/build_diagnostic_samples.py >/dev/null
printf 'Phase 6C diagnostic static contract: PASS\n'
printf 'NOTE: real MetricFlow/Dagster diagnostic evidence remains DEFERRED.\n'
