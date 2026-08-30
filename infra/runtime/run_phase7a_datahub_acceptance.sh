#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE7A_ALLOW_DATAHUB_GOVERNANCE_WRITE:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7A_ALLOW_DATAHUB_GOVERNANCE_WRITE=true explicitly." >&2
  exit 2
fi
PYTHON_BIN="${DATAHUB_PYTHON_BIN:-$ROOT/.venv-datahub/bin/python}"
[[ -x "$PYTHON_BIN" ]] || { echo "DEFERRED: DataHub Python missing at $PYTHON_BIN" >&2; exit 2; }
"$PYTHON_BIN" metadata/datahub/tools/phase7_runtime.py resolve-identities
"$PYTHON_BIN" metadata/datahub/tools/phase7_runtime.py apply-and-verify-governance
