#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ "${PHASE7A_ALLOW_RUNTIME_BOOTSTRAP:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE7A_ALLOW_RUNTIME_BOOTSTRAP=true explicitly before starting the real workstation runtime." >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  echo "REFUSED: .env is missing. Copy .env.example to .env, review local credentials/gates, then retry." >&2
  exit 2
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

PYTHON_BIN="${PHASE7_PYTHON_BIN:-python3.12}"
if command -v python3 >/dev/null 2>&1; then
  PREFLIGHT_PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PREFLIGHT_PYTHON=python
else
  echo "BLOCKED: no Python interpreter is available to run the Phase 7A readiness scan." >&2
  exit 2
fi
EVIDENCE_DIR="${PHASE7A_EVIDENCE_DIR:-.runtime/evidence/phase7a}"
mkdir -p "$EVIDENCE_DIR"

printf '\n============================================================\n'
printf 'PHASE 7A — CORE REAL RUNTIME BOOTSTRAP\n'
printf '============================================================\n'

printf '\n[1/8] Phase 6 frozen static closure\n'
bash infra/runtime/run_phase6_static_closure.sh

printf '\n[2/8] Workstation runtime preflight\n'
"$PREFLIGHT_PYTHON" infra/runtime/phase7/phase7a_preflight.py \
  --strict \
  --json-output "$EVIDENCE_DIR/preflight.json"

printf '\n[3/8] Core data plane + lakehouse acceptance\n'
bash infra/runtime/run_pre_dagster_validation.sh

printf '\n[4/8] Canonical dbt build/test acceptance\n'
PYTHON_BIN="$PYTHON_BIN" bash infra/runtime/run_dbt_validation.sh

printf '\n[5/8] Local MetricFlow compatibility acceptance\n'
MF_PYTHON_BIN="$PYTHON_BIN" bash infra/runtime/run_metricflow_validation.sh

printf '\n[6/8] Dagster isolated runtime environment\n'
"$PYTHON_BIN" -m venv .venv-dagster
# shellcheck disable=SC1091
source .venv-dagster/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-dagster.txt
export DAGSTER_HOME="$ROOT/infra/dagster"
export PYTHONPATH="$ROOT/orchestration/dagster${PYTHONPATH:+:$PYTHONPATH}"

dagster --version
dagster definitions validate \
  -m commerce_dagster.definitions \
  -a defs \
  -d "$ROOT/orchestration/dagster"

printf '\n[7/8] Phase 3C real-runtime preflight against the running data plane\n'
bash infra/runtime/run_phase3c_dagster_preflight.sh

printf '\n[8/8] Runtime evidence snapshot\n'
set +e
python infra/runtime/phase7/collect_phase7a_evidence.py \
  --output "$EVIDENCE_DIR/core_runtime.json"
EVIDENCE_RC=$?
set -e
if [[ "$EVIDENCE_RC" -ne 0 ]]; then
  echo "ERROR: Phase 7A runtime evidence snapshot is incomplete." >&2
  exit "$EVIDENCE_RC"
fi

printf '\nPhase 7A core runtime bootstrap PASSED.\n'
printf 'NOTE: this verifies the core data plane + dbt + MetricFlow + Dagster runtime readiness.\n'
printf 'DataHub bootstrap and end-to-end runtime observations remain separate follow-up acceptance steps.\n'
