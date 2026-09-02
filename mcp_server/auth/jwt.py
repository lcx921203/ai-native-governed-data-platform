"""MCP / Agent HTTP 的共享 JWT + JWKS 验证边界。

真实 HTTP Runtime 必须验证：
- JWT signature（签名）；
- issuer（签发方）；
- audience（受众）；
- expiration（过期时间）；
- subject（主体）。

V2 生产硬化：
- 一个 ``JWKSJWTVerifier`` 实例只创建一个 ``PyJWKClient``；
- JWK Set 使用有限 TTL 的进程级缓存，不再每个请求 new Client；
- 启动/Readiness 前预热 JWK Set，避免首批用户承担 JWKS Cold Fetch；
- ``cache_keys=False``，避免单个 kid 被无限期固定在函数级缓存；
- 新 kid 由 PyJWKClient 在 Miss 时自动刷新；
- 同 kid 换 Key Material 时，InvalidSignature 只允许一次受冷却时间保护的强制刷新；
- 所有认证失败继续 Fail Closed；
- 原始 Bearer Token、JWKS URL、Provider 异常文本都不向下游/公共错误传播。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any


class JWTVerificationError(RuntimeError):
    """JWT 配置缺失、JWKS 不可用或 Token 验证失败时的稳定认证错误。"""


@dataclass(frozen=True)
class VerifiedJWT:
    """JWT 验证成功后的最小身份结果。

    ``scopes: tuple[str, ...]`` 表示长度不固定、但所有元素都是 ``str``。
    原始 Bearer Token 永远不会进入该对象。
    """

    subject: str
    client_id: str
    scopes: tuple[str, ...]
    expires_at: int | None
    claims: dict[str, Any]


class JWKSJWTVerifier:
    """复用单个 PyJWKClient 的受治理 JWT Verifier。

    缓存语义：
    - 缓存的是 IdP 公布的 Public JWKS，不是用户 Token；
    - JWK Set TTL 只降低远程 JWKS Fetch 频率，不跳过签名/Claims 验证；
    - 每个 Token 仍执行 ``jwt.decode``；
    - 未知 ``kid`` 仍会触发 PyJWKClient 的 Refresh；
    - 同 ``kid`` Key Rotation 通过一次受限强制 Refresh 恢复；
    - Refresh 失败时不降级到“跳过签名”，仍 Fail Closed。
    """

    DEFAULT_JWKS_CACHE_LIFESPAN_SECONDS = 300
    DEFAULT_JWKS_TIMEOUT_SECONDS = 5
    DEFAULT_SIGNATURE_REFRESH_COOLDOWN_SECONDS = 5.0

    def __init__(
        self,
        *,
        jwks_url: str | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        algorithms: tuple[str, ...] = ("RS256", "ES256"),
        jwks_cache_lifespan_seconds: int = DEFAULT_JWKS_CACHE_LIFESPAN_SECONDS,
        jwks_timeout_seconds: int = DEFAULT_JWKS_TIMEOUT_SECONDS,
        signature_refresh_cooldown_seconds: float = (
            DEFAULT_SIGNATURE_REFRESH_COOLDOWN_SECONDS
        ),
        prewarm_jwks: bool = True,
    ):
        """构造共享 JWKS Client，并默认在服务可用前完成一次预热。

        ``prewarm_jwks=True`` 会让 Agent API 的现有 ``/health/ready`` 在构造
        Verifier 时真实验证 JWKS Endpoint 可读取。MCP HTTP 复用同一规则。

        这里不缓存 Token Decode Result；缓存只存在于 Public JWK Set。
        """

        self.jwks_url = (
            jwks_url
            or os.getenv("MCP_JWKS_URL", "")
        ).strip()
        self.issuer = (
            issuer
            or os.getenv("MCP_AUTH_ISSUER", "")
        ).strip()
        self.audience = (
            audience
            or os.getenv("MCP_AUDIENCE", "")
        ).strip()
        self.algorithms = tuple(
            str(item)
            for item in algorithms
        )

        if not (
            self.jwks_url
            and self.issuer
            and self.audience
        ):
            raise JWTVerificationError(
                "JWKS URL, issuer and audience are required for HTTP auth."
            )

        if not self.algorithms:
            raise JWTVerificationError(
                "At least one asymmetric JWT algorithm is required."
            )

        if not (
            30
            <= int(jwks_cache_lifespan_seconds)
            <= 3600
        ):
            raise JWTVerificationError(
                "JWKS cache lifespan must stay within [30, 3600] seconds."
            )
        if not (
            1
            <= int(jwks_timeout_seconds)
            <= 30
        ):
            raise JWTVerificationError(
                "JWKS HTTP timeout must stay within [1, 30] seconds."
            )
        if not (
            1.0
            <= float(
                signature_refresh_cooldown_seconds
            )
            <= 60.0
        ):
            raise JWTVerificationError(
                "JWKS signature refresh cooldown must stay within [1, 60] seconds."
            )

        try:
            import jwt
        except ImportError as exc:
            raise JWTVerificationError(
                "PyJWT[crypto] is not installed."
            ) from exc

        self._jwt = jwt
        self._lock = RLock()
        self._signature_refresh_cooldown_seconds = float(
            signature_refresh_cooldown_seconds
        )
        self._last_signature_refresh_at = float(
            "-inf"
        )

        # 关键性能修复：
        # PyJWKClient 属于 Verifier 生命周期，而不是 Request 生命周期。
        # cache_keys=False 避免 kid -> key 被无限期函数级缓存；
        # cache_jwk_set=True + lifespan 则提供有限 TTL 的 Public JWKS Cache。
        self._jwk_client = jwt.PyJWKClient(
            self.jwks_url,
            cache_keys=False,
            cache_jwk_set=True,
            lifespan=int(
                jwks_cache_lifespan_seconds
            ),
            timeout=int(
                jwks_timeout_seconds
            ),
        )

        if prewarm_jwks:
            self.warm_jwks()

    def warm_jwks(self) -> int:
        """预热 Public JWK Set，并返回可用 Key 数量。

        该方法不接受 Token，也不产生用户级缓存。
        失败时使用稳定错误，不回显 JWKS URL/网络异常详情。
        """

        try:
            with self._lock:
                jwk_set = (
                    self._jwk_client.get_jwk_set()
                )
        except Exception as exc:
            raise JWTVerificationError(
                "JWKS prewarm failed."
            ) from exc

        keys = tuple(
            getattr(
                jwk_set,
                "keys",
                (),
            )
            or ()
        )
        if not keys:
            raise JWTVerificationError(
                "JWKS did not contain a usable signing key."
            )
        return len(keys)

    def _resolve_signing_key(
        self,
        token: str,
    ):
        """在共享 JWK Set Cache 上解析当前 Token 的 kid。"""

        with self._lock:
            return (
                self._jwk_client
                .get_signing_key_from_jwt(
                    token
                )
            )

    def _refresh_after_invalid_signature(
        self,
    ) -> bool:
        """同 kid Key Material 变化时最多按 Cooldown 强制刷新一次。

        攻击者可以伪造同 kid 的错误签名，因此不能让每个 InvalidSignature
        都变成一次远程 JWKS GET。这里通过单进程 Cooldown 防止 Refresh Amplification。
        """

        with self._lock:
            now = monotonic()
            if (
                now
                - self._last_signature_refresh_at
                < self._signature_refresh_cooldown_seconds
            ):
                return False

            # 只有 Refresh 真正成功后才更新时间；
            # 失败继续 Fail Closed，且不会伪装成已完成刷新。
            self._jwk_client.get_jwk_set(
                refresh=True
            )
            self._last_signature_refresh_at = (
                now
            )
            return True

    def _decode(
        self,
        token: str,
        signing_key,
    ) -> dict[str, Any]:
        """每个请求都执行完整 Signature + Claims 验证。"""

        return self._jwt.decode(
            token,
            signing_key.key,
            algorithms=list(
                self.algorithms
            ),
            issuer=self.issuer,
            audience=self.audience,
            options={
                "require": [
                    "exp",
                    "sub",
                    "iss",
                    "aud",
                ]
            },
        )

    def verify(
        self,
        token: str,
    ) -> VerifiedJWT:
        """验证 Bearer JWT，并投影最小可信身份。

        Rotation 语义：
        1. 新 ``kid``：PyJWKClient Cache Miss 自动 Refresh；
        2. 同 ``kid`` 换 Key：第一次 InvalidSignature 触发一次受限强制 Refresh；
        3. 第二次仍失败：直接 401/Fail Closed，不继续网络重试。
        """

        try:
            signing_key = (
                self._resolve_signing_key(
                    token
                )
            )

            try:
                claims = self._decode(
                    token,
                    signing_key,
                )
            except self._jwt.InvalidSignatureError:
                refreshed = (
                    self._refresh_after_invalid_signature()
                )
                if not refreshed:
                    raise

                signing_key = (
                    self._resolve_signing_key(
                        token
                    )
                )
                claims = self._decode(
                    token,
                    signing_key,
                )

        except Exception as exc:
            # 不把 Provider URL、网络错误、Token 片段或 Crypto 详情向上游暴露。
            raise JWTVerificationError(
                "JWT verification failed."
            ) from exc

        subject = str(
            claims.get("sub")
            or ""
        ).strip()
        if not subject:
            raise JWTVerificationError(
                "JWT subject is required."
            )

        raw_scope = claims.get(
            "scope",
            "",
        )
        scopes = (
            tuple(
                str(raw_scope).split()
            )
            if isinstance(
                raw_scope,
                str,
            )
            else tuple(
                str(item)
                for item in (
                    raw_scope
                    or []
                )
            )
        )
        client_id = str(
            claims.get("client_id")
            or claims.get("azp")
            or subject
        )
        expires_at = (
            int(claims["exp"])
            if claims.get("exp")
            is not None
            else None
        )

        # Bearer Token 只存在于验证调用栈，不进入 VerifiedJWT。
        return VerifiedJWT(
            subject=subject,
            client_id=client_id,
            scopes=scopes,
            expires_at=expires_at,
            claims=dict(claims),
        )
