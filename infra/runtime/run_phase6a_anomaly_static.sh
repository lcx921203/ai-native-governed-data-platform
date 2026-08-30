#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PHASE6A_ALLOW_ANOMALY_QUERY=false
export PHASE5B_ALLOW_METRICFLOW_QUERY=false
python infra/runtime/sync_phase5_contracts.py --repair
python -m pytest -q tests/test_phase6a_governed_anomaly_detection.py tests/test_phase5_closure_contract.py
python -m compileall -q agent/anomaly_analysis
python - <<'PY'
from pathlib import Path
import yaml
root = Path('.')
for rel in [
    'agent/contracts/anomaly_detection_policy.yml',
    'agent/contracts/phase6_capability_manifest.yml',
]:
    yaml.safe_load((root / rel).read_text(encoding='utf-8'))
print('Phase 6A YAML parse: PASS')
PY
echo "Phase 6A static anomaly contract: PASS"
