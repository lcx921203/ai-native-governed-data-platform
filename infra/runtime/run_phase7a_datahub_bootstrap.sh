#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE7A_ALLOW_DATAHUB_BOOTSTRAP:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7A_ALLOW_DATAHUB_BOOTSTRAP=true explicitly." >&2
  exit 2
fi
python - <<'PY'
import json
from pathlib import Path
p=Path('.runtime/evidence/phase7a/core_runtime.json')
if not p.exists(): raise SystemExit('DEFERRED: Phase 7A core runtime evidence is missing.')
d=json.loads(p.read_text())
if d.get('runtime_verified') is not True or d.get('status')!='RUNTIME_BOOTSTRAP_VERIFIED':
    raise SystemExit('DEFERRED: Phase 7A core runtime is not verified.')
PY
DATAHUB_BIN="${DATAHUB_BIN:-$ROOT/.venv-datahub/bin/datahub}"
PYTHON_BIN="${DATAHUB_PYTHON_BIN:-$ROOT/.venv-datahub/bin/python}"
[[ -x "$DATAHUB_BIN" ]] || { echo "DEFERRED: DataHub CLI missing at $DATAHUB_BIN" >&2; exit 2; }
[[ -x "$PYTHON_BIN" ]] || { echo "DEFERRED: DataHub Python missing at $PYTHON_BIN" >&2; exit 2; }
"$PYTHON_BIN" metadata/datahub/tools/phase7_runtime.py bootstrap-definitions
"$DATAHUB_BIN" ingest -c metadata/datahub/recipes/phase7a_iceberg.yml
"$DATAHUB_BIN" ingest -c metadata/datahub/recipes/phase7a_dbt.yml
"$PYTHON_BIN" metadata/datahub/tools/phase7_runtime.py resolve-identities
