#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
python -m pytest -q tests/test_phase7c_mcp_static.py
python -m py_compile mcp_server/registry.py mcp_server/resources.py mcp_server/prompts.py mcp_server/server.py mcp_server/auth/jwt.py mcp_server/auth/profiles.py mcp_server/auth/scopes.py mcp_server/models.py
echo "Phase 7C static source closure: PASS"
echo "Runtime evidence: DEFERRED"
