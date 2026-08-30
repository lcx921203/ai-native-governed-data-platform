#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEMPLATE="$ROOT_DIR/ingestion/mysql_cdc/flink/item_store_cdc.sql.tmpl"
RUNTIME_DIR="$ROOT_DIR/.runtime/mysql-cdc"
RENDERED_SQL="$RUNTIME_DIR/item_store_cdc.sql"

mkdir -p "$RUNTIME_DIR"
python "$ROOT_DIR/ingestion/mysql_cdc/flink/render_sql.py" \
  --template "$TEMPLATE" \
  --output "$RENDERED_SQL"

: "${FLINK_HOME:?需要设置 FLINK_HOME}"

# SQL Client 运行前，Flink lib/ 需要已经安装：
# - Flink CDC MySQL 3.6 对应 Flink 1.20 的 SQL connector
# - MySQL JDBC driver
# - Iceberg Flink runtime 1.20
# 版本锁与说明见 infra/flink/README.md。
exec "$FLINK_HOME/bin/sql-client.sh" -f "$RENDERED_SQL"
