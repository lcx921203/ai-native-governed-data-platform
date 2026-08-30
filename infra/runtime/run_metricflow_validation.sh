#!/usr/bin/env bash
# MetricFlow Compatibility Runtime Acceptance
# 业务逻辑：在独立兼容 venv 中生成 Legacy Spec、构建 dbt、执行正/负语义查询并验证 Join Safety。
# 输入：Canonical Semantic/Metric YAML + 固定 Fixture + 兼容版本 requirements。
# 输出：CLI 成功/失败状态与查询结果；negative fanout query 必须失败才算安全门正常。
# API/CLI：dbt build 负责物理模型，mf validate/list/query/explain 负责 Semantic Layer Runtime。
# 工程边界：脚本存在仅表示 ACCEPTANCE DEFINED；没有执行日志不能写 Runtime PASS。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MF_DIR="$ROOT_DIR/dbt/mercaso_metricflow_compat"
VENV_DIR="$ROOT_DIR/.venv-mf"

# Prefer Python 3.12 for the local MetricFlow environment; fall back to python3.
if command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="${MF_PYTHON_BIN:-python3.12}"
else
  PYTHON_BIN="${MF_PYTHON_BIN:-python3}"
fi

cd "$ROOT_DIR"

printf '\n== MetricFlow compatibility runtime ==\n'
printf 'Canonical project stays on dbt Core 1.12 latest Semantic YAML.\n'
printf 'Local mf uses a generated legacy spec because dbt-metricflow 0.13.0 pins Core <1.12.\n'

"$PYTHON_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements-metricflow-compat.txt

printf '\n== versions ==\n'
dbt --version
mf --version || true

printf '\n== generate legacy compatibility semantic spec ==\n'
python infra/runtime/generate_metricflow_legacy.py --project-root "$ROOT_DIR"

cd "$MF_DIR"
export DBT_PROFILES_DIR="$MF_DIR"

printf '\n== 1. compatibility dbt debug/parse/build ==\n'
dbt debug --profiles-dir .
dbt parse --profiles-dir .
dbt build --full-refresh --profiles-dir .

printf '\n== 2. MetricFlow semantic validation ==\n'
mf validate-configs
mf health-checks

printf '\n== 3. Semantic inventory ==\n'
mf list entities
mf list metrics
mf list dimensions --metrics gross_sales
mf list dimensions --metrics average_order_value

printf '\n== 4. Positive query: Gross Sales by Item Category ==\n'
mf query --metrics gross_sales --group-by item__category --limit 20

printf '\n== 5. Positive query: Gross Sales by Store Region (two-hop path) ==\n'
mf query --metrics gross_sales --group-by store__region --limit 20

printf '\n== 6. Ratio query: AOV by Region ==\n'
mf query --metrics average_order_value --group-by store__region --limit 20

printf '\n== 7. Derived multi-fact query: Activity Net Sales by day ==\n'
mf query --metrics activity_net_sales --group-by metric_time__day --limit 30

printf '\n== 8. Lifecycle Conversion query: Order -> Paid within 24h ==\n'
mf query --metrics order_to_paid_24h_conversion_rate --limit 20

printf '\n== 9. Lifecycle Conversion query: Order -> Fulfillment within 3d ==\n'
mf query --metrics order_to_fulfillment_3d_conversion_rate --limit 20

printf '\n== 10. Lifecycle Conversion query: Order -> Delivered within 7d ==\n'
mf query --metrics order_to_delivered_7d_conversion_rate --limit 20

printf '\n== 11. Explain + dataflow plan ==\n'
mf query --metrics gross_sales --group-by store__region --limit 20 --explain --show-dataflow-plan

printf '\n== 12. Negative join-safety test: Order Count by Item Category must be rejected ==\n'
set +e
NEGATIVE_OUTPUT="$(mf query --metrics order_count --group-by item__category --limit 20 2>&1)"
NEGATIVE_STATUS=$?
set -e
printf '%s\n' "$NEGATIVE_OUTPUT"
if [[ "$NEGATIVE_STATUS" -eq 0 ]]; then
  printf 'ERROR: unsafe Order PRIMARY -> OrderItem FOREIGN path unexpectedly succeeded.\n' >&2
  exit 1
fi
printf 'Unsafe fanout query rejected as expected.\n'

printf '\nMetricFlow compatibility validation passed.\n'
