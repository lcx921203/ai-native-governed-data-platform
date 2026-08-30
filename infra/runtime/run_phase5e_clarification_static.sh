#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
pytest -q tests/test_phase5d_dimension_value_resolution.py tests/test_phase5e_clarification_continuation.py
python -m compileall -q agent/clarification agent/semantic_query
printf 'Phase 5E governed clarification/continuation static contract: PASS\n'
printf 'NOTE: real MetricFlow continuation execution remains DEFERRED.\n'
