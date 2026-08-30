#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
pytest -q tests/test_phase5c_dimension_value_discovery.py tests/test_phase5d_dimension_value_resolution.py
python -m compileall -q agent/dimension_values agent/dimension_resolution agent/tools
printf 'Phase 5D governed dimension-value resolution static contract: PASS\n'
printf 'NOTE: runtime-discovered dynamic values remain DEFERRED.\n'
