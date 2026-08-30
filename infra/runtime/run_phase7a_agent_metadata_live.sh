#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE7A_ALLOW_AGENT_DATAHUB_READ:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7A_ALLOW_AGENT_DATAHUB_READ=true explicitly." >&2
  exit 2
fi
PYTHON_BIN="${DATAHUB_PYTHON_BIN:-$ROOT/.venv-datahub/bin/python}"
[[ -x "$PYTHON_BIN" ]] || { echo "DEFERRED: DataHub Python missing at $PYTHON_BIN" >&2; exit 2; }
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" infra/runtime/phase7/agent_metadata_acceptance.py
