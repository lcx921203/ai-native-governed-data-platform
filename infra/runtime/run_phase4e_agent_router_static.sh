#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"; export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
python agent/build_context_samples.py >/dev/null
python agent/build_routing_samples.py >/dev/null
python agent/build_answer_samples.py >/dev/null
pytest -q tests/test_phase5_closure_contract.py -k 'tool_schema or router or phase4d'
python -m compileall -q agent/router agent/tools agent/context agent/response
printf 'Phase 4E Router/read-tool static contract: PASS\n'
