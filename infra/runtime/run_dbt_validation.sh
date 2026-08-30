#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DBT_DIR="$ROOT_DIR/dbt/mercaso_dbt"
VENV_DIR="$ROOT_DIR/.venv-dbt"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

printf '\n== dbt runtime: create isolated environment ==\n'
"$PYTHON_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-dbt.txt

printf '\n== dbt versions ==\n'
dbt --version

cd "$DBT_DIR"
export DBT_PROFILES_DIR="$DBT_DIR"

printf '\n== 1. dbt debug ==\n'
dbt debug --profiles-dir .

printf '\n== 2. dbt parse (latest Semantic Layer YAML) ==\n'
dbt parse --profiles-dir .
test -f target/semantic_manifest.json
printf 'semantic_manifest.json generated.\n'

printf '\n== 3. dbt build --full-refresh ==\n'
# dbt build executes seeds, models and data tests in DAG order.
dbt build --full-refresh --profiles-dir .

printf '\n== 4. Explicit dbt test ==\n'
# Redundant with build by design: keeps the acceptance step visible and independently repeatable.
dbt test --profiles-dir .

printf '\n== 5. Parsed semantic resources ==\n'
dbt ls --resource-type metric --profiles-dir .

printf '\n== 6. Smoke query a business mart ==\n'
dbt show --select order_items --limit 5 --profiles-dir .

printf '\nCanonical dbt build/tests validation passed.\n'
