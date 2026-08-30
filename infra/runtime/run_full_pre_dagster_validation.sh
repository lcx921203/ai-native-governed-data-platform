#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

printf '\n============================================================\n'
printf 'PRE-DAGSTER FULL VALIDATION\n'
printf '============================================================\n'

printf '\n[1/3] Storage / Catalog / Compute / Lakehouse\n'
bash infra/runtime/run_pre_dagster_validation.sh

printf '\n[2/3] Canonical dbt Core 1.12 build/tests\n'
bash infra/runtime/run_dbt_validation.sh

printf '\n[3/3] Local MetricFlow compatibility queries\n'
bash infra/runtime/run_metricflow_validation.sh

printf '\n============================================================\n'
printf 'ALL PRE-DAGSTER ACCEPTANCE STEPS PASSED.\n'
printf 'The project is now ready to introduce Dagster assets.\n'
printf '============================================================\n'
