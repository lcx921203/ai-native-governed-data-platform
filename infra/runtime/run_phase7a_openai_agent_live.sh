#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE7A_ALLOW_OPENAI_PROVIDER:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7A_ALLOW_OPENAI_PROVIDER=true explicitly." >&2
  exit 2
fi
[[ -n "${OPENAI_API_KEY:-}" ]] || { echo "DEFERRED: OPENAI_API_KEY is not configured." >&2; exit 2; }
PYTHON_BIN="${AGENT_PYTHON_BIN:-$ROOT/.venv-agent/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN="python"; fi
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" infra/runtime/phase7/openai_agent_acceptance.py
