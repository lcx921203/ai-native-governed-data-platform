#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

usage() {
  cat <<'USAGE'
Usage:
  bash infra/runtime/run_clean_room_acceptance.sh

Fresh-clone / clean-room acceptance for the current Pre-Dagster data stack.

The script:
  1. creates .env from .env.example when needed
  2. checks host prerequisites
  3. bootstraps required host Python environments when missing
  4. destroys prior local runtime state
  5. starts RustFS -> Polaris -> Spark
  6. creates Raw / Structured Source tables
  7. loads fixtures twice and normalizes twice
  8. validates runtime + dbt build/tests + MetricFlow queries
  9. validates business-version regression + golden metric results

It intentionally does NOT execute Dagster Phase 3A.
USAGE
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then usage; exit 0; fi
if [[ "$#" -gt 0 ]]; then echo "Unknown argument: $1" >&2; usage >&2; exit 2; fi

printf '\n============================================================\n'
printf 'CLEAN-ROOM ACCEPTANCE\n'
printf '============================================================\n'

if [[ ! -f .env ]]; then
  cp .env.example .env
  printf '\nCreated .env from .env.example.\n'
  printf 'Fixture acceptance uses local defaults; Shopify credentials are not required.\n'
fi

printf '\n[0/6] Host prerequisites\n'
bash infra/runtime/check_prerequisites.sh
# shellcheck disable=SC1091
source infra/runtime/_project_env.sh

printf '\n[1/6] Clean previous local runtime state\n'
bash infra/runtime/reset_local_runtime.sh --yes

printf '\n[2/6] Bootstrap host validation environments when missing\n'
if [[ ! -x .venv-ingestion/bin/python ]]; then bash infra/runtime/bootstrap_ingestion_env.sh; else printf 'Reuse existing ingestion venv: .venv-ingestion\n'; fi
if [[ ! -x .venv-dbt/bin/dbt ]]; then bash infra/runtime/bootstrap_dbt_env.sh; else printf 'Reuse existing dbt venv: .venv-dbt\n'; fi
if [[ ! -x .venv-mf/bin/mf ]]; then bash infra/runtime/bootstrap_metricflow_env.sh; else printf 'Reuse existing MetricFlow venv: .venv-mf\n'; fi

printf '\n[3/6] Render configuration from .env\n'
python3 infra/runtime/render_runtime_config.py

printf '\n[4/6] Execute complete Pre-Dagster acceptance\n'
bash infra/runtime/run_full_pre_dagster_validation.sh

printf '\n[5/6] Business-result acceptance\n'
bash infra/runtime/run_business_result_acceptance.sh

printf '\n[6/6] Final runtime state\n'
docker compose ps

printf '\n============================================================\n'
printf 'CLEAN-ROOM ACCEPTANCE PASSED\n'
printf '============================================================\n'
printf 'Validated from an empty local runtime state:\n'
printf '  Runtime -> Schema -> Fixture -> Raw -> Normalize -> dbt -> MetricFlow -> Golden Results\n'
