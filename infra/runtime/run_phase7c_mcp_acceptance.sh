#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [[ "${PHASE7C_ALLOW_MCP_RUNTIME:-false}" != "true" && "${PHASE7_ALLOW_FINAL_RUNTIME_CLOSURE:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7C_ALLOW_MCP_RUNTIME=true explicitly." >&2
  exit 2
fi
PYTHON_BIN="${MCP_PYTHON_BIN:-$ROOT/.venv-mcp/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "DEFERRED: MCP runtime environment is unavailable at $PYTHON_BIN" >&2
  exit 3
fi
# The live HTTP acceptance requires real OAuth/JWKS configuration. Static source presence never upgrades it.
for name in MCP_AUTH_ISSUER MCP_RESOURCE_URL MCP_AUDIENCE MCP_JWKS_URL MCP_ACCEPTANCE_TOKEN; do
  if [[ -z "${!name:-}" ]]; then echo "DEFERRED: $name is required for OAuth-protected MCP acceptance." >&2; exit 3; fi
done
"$PYTHON_BIN" infra/runtime/phase7/mcp_runtime_acceptance.py
