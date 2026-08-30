#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
pytest -q tests/test_phase5c_dimension_value_discovery.py
python -m compileall -q agent/dimension_values agent/tools agent/router
printf 'Phase 5C governed dimension-value static contract: PASS\n'
printf 'NOTE: MetricFlow dimension-value runtime remains DEFERRED.\n'
