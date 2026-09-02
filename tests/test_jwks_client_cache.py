"""JWKS Client Cache + Key Rotation Hardening 的真实本地契约测试。"""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric import rsa

from mcp_server.auth.jwt import (
    JWKSJWTVerifier,
    JWTVerificationError,
)


ROOT = Path(__file__).resolve().parents[1]
ISSUER = "https://jwks-cache-test.invalid"
AUDIENCE = "commerce-agent-test"


def _b64url_uint(value: int) -> str:
    """把 RSA Integer 转成 RFC 7517 Base64URLUInt。"""

    raw = value.to_bytes(
        max(
            1,
            (value.bit_length() + 7)
            // 8,
        ),
        byteorder="big",
    )
    return (
        base64.urlsafe_b64encode(
            raw
        )
        .rstrip(b"=")
        .decode("ascii")
    )


def _new_key(kid: str):
    """生成测试 RSA Private Key + Public JWK。"""

    private_key = (
        rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
    )
    numbers = (
        private_key
        .public_key()
        .public_numbers()
    )
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url_uint(
            numbers.n
        ),
        "e": _b64url_uint(
            numbers.e
        ),
    }
    return private_key, jwk


def _token(
    private_key,
    *,
    kid: str,
    subject: str = "jwks-cache-user",
) -> str:
    """签发满足真实 Verifier Claim Contract 的短期 JWT。"""

    now = datetime.now(
        timezone.utc
    )
    return jwt.encode(
        {
            "sub": subject,
            "client_id": "jwks-cache-test",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(
                now.timestamp()
            ),
            "exp": int(
                (
                    now
                    + timedelta(
                        minutes=5
                    )
                ).timestamp()
            ),
            "scope": (
                "commerce:semantic:read"
            ),
        },
        private_key,
        algorithm="RS256",
        headers={
            "kid": kid
        },
    )


@contextmanager
def _rotating_jwks_server(
    initial_jwks: dict,
):
    """启动可在测试中热切换 JWK Set 的 Loopback Server。"""

    state = {
        "jwks": initial_jwks,
        "requests": 0,
    }

    class Handler(
        BaseHTTPRequestHandler
    ):
        """返回当前 State 中的 JWK Set。"""

        def do_GET(self):
            """只允许固定 JWKS Path。"""

            if (
                self.path
                != "/.well-known/jwks.json"
            ):
                self.send_response(
                    404
                )
                self.end_headers()
                return

            state["requests"] += 1
            payload = json.dumps(
                state["jwks"],
                separators=(",", ":"),
            ).encode("utf-8")

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json",
            )
            self.send_header(
                "Content-Length",
                str(len(payload)),
            )
            self.end_headers()
            self.wfile.write(
                payload
            )

        def log_message(
            self,
            _format,
            *_args,
        ):
            """禁止把测试请求写到 CI 日志。"""

            return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        Handler,
    )
    thread = Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()

    host, port = (
        server.server_address
    )
    try:
        yield (
            f"http://{host}:{port}"
            "/.well-known/jwks.json",
            state,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(
            timeout=2
        )


def _verifier(
    jwks_url: str,
    *,
    cooldown: float = 5.0,
) -> JWKSJWTVerifier:
    """构造真实 RS256 Verifier，默认在构造阶段预热 JWKS。"""

    return JWKSJWTVerifier(
        jwks_url=jwks_url,
        issuer=ISSUER,
        audience=AUDIENCE,
        algorithms=("RS256",),
        jwks_cache_lifespan_seconds=300,
        jwks_timeout_seconds=5,
        signature_refresh_cooldown_seconds=cooldown,
        prewarm_jwks=True,
    )


def test_repeated_tokens_reuse_one_prewarmed_jwk_set_without_refetch():
    """同一 kid 的正常请求不能每次重新 GET JWKS。"""

    private_key, jwk = (
        _new_key("kid-stable")
    )

    with _rotating_jwks_server(
        {"keys": [jwk]}
    ) as (
        jwks_url,
        state,
    ):
        verifier = _verifier(
            jwks_url
        )

        # Constructor Prewarm 只做一次真实 JWKS GET。
        assert state["requests"] == 1

        first = verifier.verify(
            _token(
                private_key,
                kid="kid-stable",
                subject="user-1",
            )
        )
        second = verifier.verify(
            _token(
                private_key,
                kid="kid-stable",
                subject="user-2",
            )
        )

        assert first.subject == "user-1"
        assert second.subject == "user-2"

        # 请求级 Crypto/Claim Verification 仍执行，但 Public JWK Set 不重复拉取。
        assert state["requests"] == 1


def test_unknown_kid_rotation_refreshes_shared_client_and_accepts_new_key():
    """标准 Key Rotation 使用新 kid 时必须自动 Refresh，而不是等 TTL。"""

    key1, jwk1 = _new_key(
        "kid-v1"
    )
    key2, jwk2 = _new_key(
        "kid-v2"
    )

    with _rotating_jwks_server(
        {"keys": [jwk1]}
    ) as (
        jwks_url,
        state,
    ):
        verifier = _verifier(
            jwks_url
        )
        assert (
            verifier.verify(
                _token(
                    key1,
                    kid="kid-v1",
                )
            ).subject
            == "jwks-cache-user"
        )
        assert state["requests"] == 1

        # IdP 发布新 kid；PyJWKClient 在 Cache Miss 时必须立即刷新。
        state["jwks"] = {
            "keys": [jwk2]
        }

        rotated = verifier.verify(
            _token(
                key2,
                kid="kid-v2",
                subject="rotated-user",
            )
        )

        assert (
            rotated.subject
            == "rotated-user"
        )
        assert state["requests"] == 2


def test_same_kid_key_material_rotation_forces_one_refresh_and_recovers():
    """少数 IdP 复用 kid 换 Key Material 时，InvalidSignature 后允许一次安全 Refresh。"""

    key1, jwk1 = _new_key(
        "kid-same"
    )
    key2, jwk2 = _new_key(
        "kid-same"
    )

    with _rotating_jwks_server(
        {"keys": [jwk1]}
    ) as (
        jwks_url,
        state,
    ):
        verifier = _verifier(
            jwks_url
        )
        verifier.verify(
            _token(
                key1,
                kid="kid-same",
            )
        )
        assert state["requests"] == 1

        state["jwks"] = {
            "keys": [jwk2]
        }

        rotated = verifier.verify(
            _token(
                key2,
                kid="kid-same",
                subject="same-kid-user",
            )
        )

        assert (
            rotated.subject
            == "same-kid-user"
        )
        assert state["requests"] == 2


def test_invalid_signature_refresh_is_cooldown_bounded_against_network_amplification():
    """恶意同 kid 错签名不能让每次 401 都触发远程 JWKS Fetch。"""

    trusted_key, trusted_jwk = (
        _new_key("kid-attack")
    )
    attacker_key, _ = _new_key(
        "kid-attack"
    )

    with _rotating_jwks_server(
        {"keys": [trusted_jwk]}
    ) as (
        jwks_url,
        state,
    ):
        verifier = _verifier(
            jwks_url,
            cooldown=60.0,
        )
        assert state["requests"] == 1

        bad_token = _token(
            attacker_key,
            kid="kid-attack",
        )

        with pytest.raises(
            JWTVerificationError
        ):
            verifier.verify(
                bad_token
            )

        # 第一次 InvalidSignature 可以尝试一次强制 Refresh。
        assert state["requests"] == 2

        with pytest.raises(
            JWTVerificationError
        ):
            verifier.verify(
                bad_token
            )

        # Cooldown 内第二次错误签名不得继续打 JWKS Endpoint。
        assert state["requests"] == 2

        # 正常旧 Key 仍然可以通过；Fail Closed 不等于破坏已有正确验证。
        assert (
            verifier.verify(
                _token(
                    trusted_key,
                    kid="kid-attack",
                    subject="trusted-user",
                )
            ).subject
            == "trusted-user"
        )


def test_jwks_cache_policy_matches_runtime_defaults_and_privacy_boundary():
    """治理 Policy 必须和 Verifier Default/安全语义一致。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/jwks_verifier_policy.yml"
        ).read_text(
            encoding="utf-8"
        )
    )

    assert policy["version"] == 1
    assert (
        policy["mode"]
        == "shared_public_keyset_cache_with_rotation_refresh"
    )

    defaults = policy[
        "defaults"
    ]
    assert (
        defaults[
            "jwks_cache_lifespan_seconds"
        ]
        == JWKSJWTVerifier.DEFAULT_JWKS_CACHE_LIFESPAN_SECONDS
    )
    assert (
        defaults[
            "jwks_http_timeout_seconds"
        ]
        == JWKSJWTVerifier.DEFAULT_JWKS_TIMEOUT_SECONDS
    )
    assert (
        defaults[
            "same_kid_signature_refresh_cooldown_seconds"
        ]
        == JWKSJWTVerifier.DEFAULT_SIGNATURE_REFRESH_COOLDOWN_SECONDS
    )
    assert defaults[
        "cache_keys"
    ] is False
    assert defaults[
        "prewarm_jwks"
    ] is True

    principles = policy[
        "principles"
    ]
    for key in (
        "bearer_token_is_never_cached",
        "decoded_token_result_is_never_cached",
        "unknown_kid_triggers_library_refresh",
        "forced_refresh_has_cooldown",
        "refresh_failure_never_disables_signature_verification",
        "issuer_audience_exp_subject_remain_required",
        "jwks_url_is_not_exposed_in_public_auth_error",
    ):
        assert (
            principles[key]
            is True
        )
