#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT"
python -m pytest -q tests/test_phase7a_runtime_bootstrap.py
python -m py_compile infra/runtime/phase7/phase7a_preflight.py infra/runtime/phase7/collect_phase7a_evidence.py
for f in infra/runtime/*.sh; do bash -n "$f"; done
printf 'Phase 7A static/source closure PASS. Runtime evidence remains DEFERRED.\n'
