#!/usr/bin/env bash
set -euo pipefail

# Production Agent API 启动入口。
#
# 必需认证配置：
#   AGENT_API_JWKS_URL
#   AGENT_API_AUTH_ISSUER
#   AGENT_API_AUDIENCE
#
# Runtime Renderer：
#   AGENT_RENDERER_MODE=deterministic|openai
#   PHASE4G_ALLOW_OPENAI_CALL=true   # 仅 live OpenAI 模式需要
#
# Append-only Audit：生产脚本默认开启，写失败 Fail Closed。
export AGENT_AUDIT_MODE="${AGENT_AUDIT_MODE:-jsonl}"
export AGENT_AUDIT_PATH="${AGENT_AUDIT_PATH:-.runtime/agent/audit.jsonl}"
export AGENT_AUDIT_FAILURE_MODE="${AGENT_AUDIT_FAILURE_MODE:-fail_closed}"

# Traffic Guard 初始护栏：这些数字不是压测后的正式 SLO。
export AGENT_API_REQUEST_TIMEOUT_SECONDS="${AGENT_API_REQUEST_TIMEOUT_SECONDS:-60}"
export AGENT_API_GLOBAL_CONCURRENCY="${AGENT_API_GLOBAL_CONCURRENCY:-16}"
export AGENT_API_TENANT_CONCURRENCY="${AGENT_API_TENANT_CONCURRENCY:-4}"
export AGENT_API_SUBJECT_RPM="${AGENT_API_SUBJECT_RPM:-30}"
export AGENT_API_TENANT_RPM="${AGENT_API_TENANT_RPM:-120}"
export AGENT_API_MAX_TRACKED_IDENTITIES="${AGENT_API_MAX_TRACKED_IDENTITIES:-10000}"

# Backend：
#   local -> 只允许单 Worker；适合本地/单进程部署。
#   redis -> 多 Worker / 多 Pod 共用 Rate + Concurrency State。
export AGENT_API_TRAFFIC_BACKEND="${AGENT_API_TRAFFIC_BACKEND:-local}"

workers="${AGENT_API_WORKERS:-1}"
if ! [[ "${workers}" =~ ^[1-9][0-9]*$ ]]; then
  echo "AGENT_API_WORKERS must be a positive integer." >&2
  exit 2
fi

if [[ "${AGENT_API_TRAFFIC_BACKEND}" == "local" && "${workers}" -gt 1 ]]; then
  echo "Refusing multi-worker Agent API with process-local traffic guard." >&2
  echo "Set AGENT_API_TRAFFIC_BACKEND=redis and AGENT_API_REDIS_URL." >&2
  exit 2
fi

if [[ "${AGENT_API_TRAFFIC_BACKEND}" == "redis" ]]; then
  if [[ -z "${AGENT_API_REDIS_URL:-}" ]]; then
    echo "AGENT_API_REDIS_URL is required for redis traffic backend." >&2
    exit 2
  fi

  # 不输出 Redis URL，避免 Credential 出现在启动日志。
  if ! python -c 'import redis.asyncio' >/dev/null 2>&1; then
    echo "Redis Python client is missing." >&2
    echo "Install requirements-agent-redis.txt." >&2
    exit 2
  fi

  export AGENT_API_REDIS_NAMESPACE="${AGENT_API_REDIS_NAMESPACE:-commerce:agent:traffic:v2}"
  export AGENT_API_REDIS_LEASE_TTL_SECONDS="${AGENT_API_REDIS_LEASE_TTL_SECONDS:-90}"
  export AGENT_API_REDIS_HEARTBEAT_SECONDS="${AGENT_API_REDIS_HEARTBEAT_SECONDS:-20}"
  export AGENT_API_REDIS_OPERATION_TIMEOUT_SECONDS="${AGENT_API_REDIS_OPERATION_TIMEOUT_SECONDS:-1}"
elif [[ "${AGENT_API_TRAFFIC_BACKEND}" != "local" ]]; then
  echo "Unsupported AGENT_API_TRAFFIC_BACKEND." >&2
  exit 2
fi

exec uvicorn agent.api.main:app \
  --host "${AGENT_API_HOST:-0.0.0.0}" \
  --port "${AGENT_API_PORT:-8080}" \
  --workers "${workers}"
