#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"; export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
python - <<'PY'
from pathlib import Path
import yaml
root=Path('.')
for p in sorted((root/'metadata/datahub/governance').glob('*.yml')): yaml.safe_load(p.read_text())
print('Phase 4B governance YAML parse: PASS')
PY
python -m compileall -q agent metadata/datahub/tools
printf 'Phase 4B governance static contract: PASS\n'
printf 'NOTE: real DataHub governance ingestion remains DEFERRED.\n'
