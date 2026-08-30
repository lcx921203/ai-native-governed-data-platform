"""Commerce MCP 的认证与授权辅助模块。

HTTP Runtime 使用 OAuth Resource Server + JWT 校验；stdio 仅依赖本地进程边界。
Token 只在 MCP 边界校验，不允许继续透传给下游工具。
"""
