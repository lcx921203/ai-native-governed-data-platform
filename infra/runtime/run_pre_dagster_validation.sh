#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

printf '\n== 0. Static fixture validation ==\n'
python lakehouse/jobs/validate_shopify_fixtures.py

printf '\n== 1. Start RustFS + Polaris + Spark Thrift ==\n'
docker compose up -d --wait --wait-timeout 300 rustfs bucket-setup polaris polaris-setup spark-thrift

docker compose ps

printf '\n== 2. Spark / Polaris / Iceberg / RustFS smoke test ==\n'
docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-sql \
  -f /opt/project/infra/runtime/smoke_test.sql

printf '\n== 3. Create Raw + Structured Source tables ==\n'
docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-sql \
  -f /opt/project/lakehouse/ddl/001_raw.sql

docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-sql \
  -f /opt/project/lakehouse/ddl/002_shopify_source.sql

printf '\n== 4. Load fixtures twice (simulate at-least-once overlap) ==\n'
docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-submit \
  /opt/project/ingestion/shopify/load_fixtures.py

docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-submit \
  /opt/project/ingestion/shopify/load_fixtures.py

printf '\n== 5. Normalize twice (verify idempotent MERGE) ==\n'
docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-submit \
  /opt/project/lakehouse/jobs/normalize_shopify_orders.py

docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-submit \
  /opt/project/lakehouse/jobs/normalize_shopify_orders.py

printf '\n== 6. Runtime assertions ==\n'
docker compose exec -T spark-thrift \
  /opt/spark/bin/spark-submit \
  /opt/project/lakehouse/jobs/validate_runtime.py

printf '\nPre-Dagster infrastructure + lakehouse runtime validation passed.\n'
