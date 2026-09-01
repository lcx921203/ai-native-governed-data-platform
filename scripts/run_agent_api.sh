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

# Process-local Traffic Guard 初始护栏。
# 这些数值不是压测后的正式 SLO；多 Worker / 多 Pod 需要共享限流基础设施。
export AGENT_API_REQUEST_TIMEOUT_SECONDS="${AGENT_API_REQUEST_TIMEOUT_SECONDS:-60}"
export AGENT_API_GLOBAL_CONCURRENCY="${AGENT_API_GLOBAL_CONCURRENCY:-16}"
export AGENT_API_TENANT_CONCURRENCY="${AGENT_API_TENANT_CONCURRENCY:-4}"
export AGENT_API_SUBJECT_RPM="${AGENT_API_SUBJECT_RPM:-30}"
export AGENT_API_TENANT_RPM="${AGENT_API_TENANT_RPM:-120}"
export AGENT_API_MAX_TRACKED_IDENTITIES="${AGENT_API_MAX_TRACKED_IDENTITIES:-10000}"

exec uvicorn agent.api.main:app \
  --host "${AGENT_API_HOST:-0.0.0.0}" \
  --port "${AGENT_API_PORT:-8080}" \
  --workers "${AGENT_API_WORKERS:-1}"
