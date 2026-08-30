#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE7A_ALLOW_AGENT_DAGSTER_READ:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7A_ALLOW_AGENT_DAGSTER_READ=true explicitly." >&2
  exit 2
fi
PYTHON_BIN="${DAGSTER_PYTHON_BIN:-$ROOT/.venv-dagster/bin/python}"
[[ -x "$PYTHON_BIN" ]] || { echo "DEFERRED: Dagster Python missing at $PYTHON_BIN" >&2; exit 2; }
export DAGSTER_HOME="${DAGSTER_HOME:-$ROOT/infra/dagster}"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" infra/runtime/phase7/dagster_operational_acceptance.py
