#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DAGSTER_HOME="${DAGSTER_HOME:-$ROOT_DIR/infra/dagster}"
export PYTHONPATH="$ROOT_DIR/orchestration/dagster${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT_DIR/orchestration/dagster"
exec dagster dev -m commerce_dagster.definitions -a defs -h 127.0.0.1 -p 3000
