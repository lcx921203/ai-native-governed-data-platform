#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
pytest -q tests/test_phase5a_semantic_query.py tests/test_phase5b_semantic_filters_multi_metric.py
python -m compileall -q agent/semantic_query agent/router agent/response
printf 'Phase 5B governed filters/multi-metric static contract: PASS\n'
printf 'NOTE: real MetricFlow/Spark/Polaris numeric evidence remains DEFERRED.\n'
