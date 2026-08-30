#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
python agent/build_comparative_breakdown_samples.py >/dev/null
pytest -q \
  tests/test_phase5b_semantic_filters_multi_metric.py \
  tests/test_phase5d_dimension_value_resolution.py \
  tests/test_phase5e_clarification_continuation.py \
  tests/test_phase5f_governed_analysis_session.py \
  tests/test_phase5g_governed_time_comparison.py \
  tests/test_phase5h_comparative_breakdown.py
python -m compileall -q agent
printf 'Phase 5H governed comparative-breakdown static closure: PASS\n'
printf 'NOTE: real MetricFlow/Spark/Polaris grouped comparison and contribution evidence remains DEFERRED.\n'
