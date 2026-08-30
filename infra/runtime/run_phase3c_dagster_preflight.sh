#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export DAGSTER_HOME="${DAGSTER_HOME:-$ROOT_DIR/infra/dagster}"
export PYTHONPATH="$ROOT_DIR/orchestration/dagster${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT_DIR"

echo "[1/20] Python/source contracts"
python -m unittest \
  tests.test_phase3c_pure_contract \
  tests.test_phase3c_source_wiring \
  tests.test_shopify_source_window_contract \
  tests.test_phase3c_r01_contract \
  tests.test_phase3c_r02_contract \
  tests.test_phase3c_r03_contract \
  tests.test_phase3c_r04_contract \
  tests.test_phase3c_r05_contract \
  tests.test_phase3c_r06_contract \
  tests.test_phase3c_r07_contract \
  tests.test_phase3c_r08_contract \
  tests.test_phase3c_r09_contract \
  tests.test_phase3c_r10_contract \
  tests.test_phase3c_r11_contract \
  tests.test_phase3c_r12_contract \
  tests.test_phase3c_r13_contract \
  tests.test_phase3c_closure_contract

echo "[2/20] Hand-authored recovery scenario oracle"
python -m acceptance.phase3c.evaluate

echo "[3/20] Docker Compose syntax"
docker compose config >/dev/null

echo "[4/20] dbt parse"
dbt parse --project-dir "$ROOT_DIR/dbt/mercaso_dbt" --profiles-dir "$ROOT_DIR/dbt/mercaso_dbt"

echo "[5/20] Dagster Definitions"
dagster definitions validate \
  -m commerce_dagster.definitions \
  -a defs \
  -d "$ROOT_DIR/orchestration/dagster"

echo "[6/20] R01-A historical schedule definition"
python "$ROOT_DIR/acceptance/phase3c/r01_schedule_definition.py"

echo "[7/20] R02-A missed-schedule sensor definition"
python "$ROOT_DIR/acceptance/phase3c/r02_missed_schedule.py"

echo "[8/20] R03-A infrastructure recovery definition runtime"
python "$ROOT_DIR/acceptance/phase3c/r03_infrastructure_recovery.py"

echo "[9/20] R04-A bounded infrastructure wait"
python "$ROOT_DIR/acceptance/phase3c/r04_infrastructure_still_down.py"

echo "[10/20] Data-plane services"
docker compose ps

echo "[11/20] R05-A dbt data-contract failure guard"
python "$ROOT_DIR/acceptance/phase3c/r05_data_contract_failure.py"

echo "[12/20] R06-A deterministic project/code failure guard"
python "$ROOT_DIR/acceptance/phase3c/r06_deterministic_code_failure.py"

echo "[13/20] R07-A duplicate recovery active-owner guard"
python "$ROOT_DIR/acceptance/phase3c/r07_duplicate_recovery_guard.py"

echo "[14/20] R08-A replay-budget exhaustion guard"
python "$ROOT_DIR/acceptance/phase3c/r08_replay_budget_exhausted.py"

echo "[15/20] R09-A success-vs-completeness guard"
python "$ROOT_DIR/acceptance/phase3c/r09_success_incomplete_partition.py"

echo "[16/20] R10-A current-completeness-over-history guard"
python "$ROOT_DIR/acceptance/phase3c/r10_partition_already_complete.py"

echo "[17/20] R11-A freshness-budget guard"
python "$ROOT_DIR/acceptance/phase3c/r11_freshness_guard.py"

echo "[18/20] R12-A unknown-failure fail-closed guard"
python "$ROOT_DIR/acceptance/phase3c/r12_unknown_failure_fail_closed.py"

echo "[19/20] R13-A transient-runtime timeout recovery"
python "$ROOT_DIR/acceptance/phase3c/r13_transient_runtime_recovery.py"

echo "[20/20] R01 verifier import"
python "$ROOT_DIR/acceptance/phase3c/r01_normal_schedule.py" --help >/dev/null

echo "Phase 3C preflight PASS"
