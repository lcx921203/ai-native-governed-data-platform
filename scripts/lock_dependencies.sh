#!/usr/bin/env bash
set -euo pipefail

# 依赖锁生成入口。
#
# 输入：组件名（agent / dagster / ... / ci / all）。
# 输出：完整传递依赖 + SHA-256 wheel/sdist hashes 的 requirements lock。
#
# 为什么不把所有组件合成一个 lock：不同运行时存在真实的依赖边界。
# 例如 MetricFlow compatibility 与 canonical dbt 的 dbt-core 版本不同，
# Dagster + dagster-dbt 与 DataHub[dbt] 也要求互不兼容的 sqlglot 版本。
# 强行合并会制造无法解析的“超级环境”，因此按 Runtime Boundary 独立锁定。
#
# agent-redis 是可选的生产共享流量 Backend：
# - 不进入默认 `all`，避免 Local Agent Runtime 被迫安装 Redis Client；
# - Real Redis Acceptance Job 会显式解析它，并从临时 hash lock 安装。

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
    agent-redis) echo "requirements-agent-redis.txt" ;;
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
