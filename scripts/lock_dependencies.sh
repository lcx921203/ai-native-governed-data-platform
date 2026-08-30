#!/usr/bin/env bash
set -euo pipefail

# 依赖锁生成入口。
#
# 输入：组件名（agent / dagster / ... / ci / all）。
# 输出：完整传递依赖 + SHA-256 wheel/sdist hashes 的 requirements lock。
#
# 为什么不把所有组件合成一个 lock：MetricFlow compatibility 与 canonical dbt
# 对 dbt-core 的版本要求不同，强行合并会抹掉真实的 Runtime Boundary。

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PYTHON_VERSION="3.11"
PYTHON_PLATFORM="x86_64-unknown-linux-gnu"
EXCLUDE_NEWER="2026-08-21T00:00:00Z"
OUTPUT_DIR="${LOCK_OUTPUT_DIR:-requirements/locks}"
UV_BIN="${UV_BIN:-uv}"
mkdir -p "${OUTPUT_DIR}"

components=(
  agent
  dagster
  datahub
  dbt
  mcp
  metricflow-compat
  rag
  serving
  streaming
  ci
)

input_for() {
  case "$1" in
    agent) echo "requirements-agent.txt" ;;
    dagster) echo "requirements-dagster.txt" ;;
    datahub) echo "requirements-datahub.txt" ;;
    dbt) echo "requirements-dbt.txt" ;;
    mcp) echo "requirements-mcp.txt" ;;
    metricflow-compat) echo "requirements-metricflow-compat.txt" ;;
    rag) echo "requirements-rag.txt" ;;
    serving) echo "requirements-serving.txt" ;;
    streaming) echo "requirements-streaming.txt" ;;
    ci) echo "requirements-ci.txt" ;;
    *) return 1 ;;
  esac
}

lock_one() {
  local component="$1"
  local input
  input="$(input_for "${component}")" || {
    echo "Unknown component: ${component}" >&2
    exit 2
  }
  local output="${OUTPUT_DIR}/${component}-py311-linux.lock.txt"

  echo "[lock] ${component}: ${input} -> ${output}"
  "${UV_BIN}" pip compile "${input}" \
    --python-version "${PYTHON_VERSION}" \
    --python-platform "${PYTHON_PLATFORM}" \
    --resolution highest \
    --exclude-newer "${EXCLUDE_NEWER}" \
    --generate-hashes \
    --output-file "${output}" \
    --custom-compile-command "./scripts/lock_dependencies.sh ${component}"
}

requested="${1:-all}"
if [[ "${requested}" == "all" ]]; then
  for component in "${components[@]}"; do
    lock_one "${component}"
  done
else
  lock_one "${requested}"
fi
