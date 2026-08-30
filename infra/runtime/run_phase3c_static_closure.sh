#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

TEST_MODULES=(
  tests.test_phase3c_pure_contract
  tests.test_phase3c_source_wiring
  tests.test_shopify_source_window_contract
  tests.test_phase3c_r01_contract
  tests.test_phase3c_r02_contract
  tests.test_phase3c_r03_contract
  tests.test_phase3c_r04_contract
  tests.test_phase3c_r05_contract
  tests.test_phase3c_r06_contract
  tests.test_phase3c_r07_contract
  tests.test_phase3c_r08_contract
  tests.test_phase3c_r09_contract
  tests.test_phase3c_r10_contract
  tests.test_phase3c_r11_contract
  tests.test_phase3c_r12_contract
  tests.test_phase3c_r13_contract
  tests.test_phase3c_closure_contract
)

echo "[1/4] Phase 3C pure/static/source contracts"
python -m unittest "${TEST_MODULES[@]}"

echo "[2/4] Recovery scenario oracle"
python -m acceptance.phase3c.evaluate

echo "[3/4] Python syntax"
python -m compileall -q \
  orchestration/dagster/commerce_dagster \
  acceptance/phase3c \
  ingestion/shopify \
  lakehouse/jobs \
  tests

echo "[4/4] Shell syntax"
for script in infra/runtime/*.sh; do
  bash -n "$script"
done

echo "Phase 3C static closure PASS"
echo "NOTE: This is not Dagster/dbt/Docker Runtime Acceptance."
