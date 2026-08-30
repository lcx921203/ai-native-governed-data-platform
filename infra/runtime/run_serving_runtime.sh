#!/usr/bin/env bash
# Local runtime path for fixed MetricFlow -> Iceberg Serving -> Trino -> FastAPI.
# 只有显式开启 Runtime Acceptance Gate 才会生成最终 Serving Runtime Evidence。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PARTITION_KEY="${1:-2026-08-05}"
cd "$ROOT_DIR"

if [[ "${SERVING_ALLOW_RUNTIME_ACCEPTANCE:-false}" != "true" ]]; then
  echo "REFUSED: set SERVING_ALLOW_RUNTIME_ACCEPTANCE=true explicitly." >&2
  exit 2
fi

if [[ ! -x .venv-mf/bin/mf ]]; then
  echo "MetricFlow compatibility venv is missing. Run ./infra/runtime/run_metricflow_validation.sh first." >&2
  exit 2
fi

export SERVING_ALLOW_METRIC_EXPORT=true

docker compose up -d rustfs bucket-setup polaris polaris-setup spark-thrift trino serving-api
python -m serving.export_cli --partition-key "$PARTITION_KEY" --materialize

docker compose exec -T trino trino --execute \
  "SELECT * FROM iceberg.serving.bi_daily_executive WHERE business_date = DATE '$PARTITION_KEY' ORDER BY region"

curl --fail "http://localhost:${SERVING_API_PORT:-8081}/health/ready"
curl --fail "http://localhost:${SERVING_API_PORT:-8081}/api/v1/executive/daily?business_date=$PARTITION_KEY"

PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}" python infra/runtime/serving_runtime_acceptance.py --partition-key "$PARTITION_KEY"
