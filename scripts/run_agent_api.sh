#!/usr/bin/env bash
set -euo pipefail

# Production Agent API 本地/容器启动入口。
#
# 必需认证配置：
#   AGENT_API_JWKS_URL
#   AGENT_API_AUTH_ISSUER
#   AGENT_API_AUDIENCE
#
# Agent Runtime 仍复用已有部署门：
#   AGENT_RENDERER_MODE=deterministic|openai
#   PHASE4G_ALLOW_OPENAI_CALL=true   # 仅 live OpenAI 模式需要
#
# 工程边界：本脚本不会写入任何默认 JWT secret，也不会自动关闭认证。
exec uvicorn agent.api.main:app \
  --host "${AGENT_API_HOST:-0.0.0.0}" \
  --port "${AGENT_API_PORT:-8080}" \
  --workers "${AGENT_API_WORKERS:-1}"
