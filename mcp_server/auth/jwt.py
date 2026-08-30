"""MCP Streamable HTTP 的 JWT 验证边界。

真实 HTTP Runtime 必须验证 JWT signature / issuer / audience / expiration / subject；
验证后的最小身份信息可以进入 MCPPrincipal，但原始 Bearer Token 禁止向 Governed Tool 透传。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class JWTVerificationError(RuntimeError):
    """JWT 配置缺失或 Token 验证失败时的显式认证错误。"""


@dataclass(frozen=True)
class VerifiedJWT:
    """JWT 验证成功后的最小身份结果。

    ``scopes: tuple[str, ...]`` 中的 ``...`` 是 Python typing 的真实语法，
    表示长度不固定、但所有元素都是 ``str`` 的 tuple，不是省略代码。
    """

    subject: str
    client_id: str
    scopes: tuple[str, ...]
    expires_at: int | None
    claims: dict[str, Any]


class JWKSJWTVerifier:
    """使用 JWKS 验证 JWT，并只返回受控身份信息。

    Framework/API：PyJWT 的 ``PyJWKClient`` 根据 ``kid`` 从 JWKS 找签名公钥；
    ``jwt.decode`` 同时校验算法、issuer、audience 与必需 claims。

    工程边界：Token 不会出现在返回对象里，也不会透传给 DataHub / Dagster / MetricFlow。
    """

    def __init__(self, *, jwks_url: str | None = None, issuer: str | None = None, audience: str | None = None, algorithms: tuple[str, ...] = ("RS256", "ES256")):
        """读取 OAuth Resource Server 的 JWKS / issuer / audience 配置。

        HTTP Auth 缺任何一项都直接失败，不允许悄悄退回未认证模式。
        """
        self.jwks_url = jwks_url or os.getenv("MCP_JWKS_URL", "")
        self.issuer = issuer or os.getenv("MCP_AUTH_ISSUER", "")
        self.audience = audience or os.getenv("MCP_AUDIENCE", "")
        self.algorithms = algorithms
        if not self.jwks_url or not self.issuer or not self.audience:
            raise JWTVerificationError("MCP_JWKS_URL, MCP_AUTH_ISSUER and MCP_AUDIENCE are required for HTTP auth")

    def verify(self, token: str) -> VerifiedJWT:
        """验证一个 Bearer JWT，并提取 subject / client_id / scopes。

        输入是原始 Token；输出只保留身份与 claims。任何签名、issuer、audience、expiration
        或 subject 异常都抛 JWTVerificationError，调用方随后按未授权处理。
        """
        try:
            import jwt
        except ImportError as exc:
            raise JWTVerificationError("PyJWT[crypto] is not installed; install requirements-mcp.txt") from exc
        try:
            signing_key = jwt.PyJWKClient(self.jwks_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except Exception as exc:
            raise JWTVerificationError(f"JWT verification failed: {exc}") from exc
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise JWTVerificationError("JWT subject is required")
        raw_scope = claims.get("scope", "")
        scopes = tuple(str(raw_scope).split()) if isinstance(raw_scope, str) else tuple(str(x) for x in (raw_scope or []))
        client_id = str(claims.get("client_id") or claims.get("azp") or subject)
        expires_at = int(claims["exp"]) if claims.get("exp") is not None else None
        # 安全边界：Bearer Token 只在 MCP 边界验证，不放进 claims 或返回对象继续向下游传递。
        return VerifiedJWT(subject=subject, client_id=client_id, scopes=scopes, expires_at=expires_at, claims=dict(claims))
