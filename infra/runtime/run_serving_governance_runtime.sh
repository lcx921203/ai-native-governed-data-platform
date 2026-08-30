#!/usr/bin/env bash
# Serving Consumer Governance Runtime：OpenAPI ingestion → exact endpoint identity → governance/lineage verify-all。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

DATAHUB_BIN="${DATAHUB_BIN:-$ROOT/.venv-datahub/bin/datahub}"
PYTHON_BIN="${DATAHUB_PYTHON_BIN:-$ROOT/.venv-datahub/bin/python}"
[[ -x "$DATAHUB_BIN" ]] || { echo "DEFERRED: DataHub CLI missing at $DATAHUB_BIN" >&2; exit 3; }
[[ -x "$PYTHON_BIN" ]] || { echo "DEFERRED: DataHub Python missing at $PYTHON_BIN" >&2; exit 3; }

: "${DATAHUB_GMS_URL:=http://localhost:8080}"
export DATAHUB_GMS_URL

# 三个 mutation gate 必须由操作者显式打开；脚本不会替用户扩大治理写权限。
for gate in \
  SERVING_GOVERNANCE_ALLOW_DATAHUB_WRITE \
  SERVING_GOVERNANCE_ALLOW_LINEAGE_WRITE \
  SERVING_GOVERNANCE_ALLOW_CONSUMER_WRITE; do
  if [[ "${!gate:-false}" != "true" ]]; then
    echo "REFUSED: set $gate=true explicitly." >&2
    exit 2
  fi
done

# OpenAPI ingestion 后 DataHub 才能产生 Endpoint Dataset Identity；exact URN 必须由操作者从实体本身取得。
if [[ -z "${SERVING_API_EXECUTIVE_DAILY_URN:-}" || -z "${SERVING_API_REGION_DAILY_URN:-}" ]]; then
  echo "DEFERRED: set exact SERVING_API_EXECUTIVE_DAILY_URN and SERVING_API_REGION_DAILY_URN after OpenAPI ingestion." >&2
  exit 3
fi

"$PYTHON_BIN" metadata/datahub/tools/build_serving_governance_projection.py
"$DATAHUB_BIN" ingest -c metadata/datahub/recipes/serving_api_openapi.yml
"$PYTHON_BIN" metadata/datahub/tools/resolve_serving_consumer_identities.py \
  --endpoint "executive_daily=${SERVING_API_EXECUTIVE_DAILY_URN}" \
  --endpoint "region_daily=${SERVING_API_REGION_DAILY_URN}"
"$PYTHON_BIN" metadata/datahub/tools/serving_runtime.py verify-all
