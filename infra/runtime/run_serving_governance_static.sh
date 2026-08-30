#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

python -m serving.api.export_openapi >/dev/null
python metadata/datahub/tools/build_serving_governance_projection.py >/dev/null
python -m pytest -q tests/test_serving_datahub_governance.py
