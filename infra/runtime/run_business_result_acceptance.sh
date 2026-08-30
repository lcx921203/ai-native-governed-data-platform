#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

printf '\n============================================================\n'
printf 'BUSINESS RESULT ACCEPTANCE\n'
printf '============================================================\n'

printf '\n[1/2] Business Version A -> B -> A integration scenario\n'
docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-submit \
  /opt/project/lakehouse/jobs/validate_business_version_rollback.py

printf '\n[2/2] MetricFlow golden business results\n'
# shellcheck disable=SC1091
source infra/runtime/_metricflow_runtime_env.sh
python tests/validate_metricflow_golden_results.py

printf '\nBUSINESS RESULT ACCEPTANCE PASSED\n'
