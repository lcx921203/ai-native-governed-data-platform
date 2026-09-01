"""HTTP JWT -> RequestContext 的可信身份映射边界。

Bearer Token 仍只交给 JWKS Verifier；HTTP 层不再维护独立的 claim 映射规则，
而是与 MCP 共同复用 ``TrustedClaimsContextMapper``。

这样 HTTP Agent API 与远程 MCP 对 tenant_id、对象 Allowlist、Dimension Scope
采用同一个 Fail-Closed Contract。
"""

from __future__ import annotations

from pathlib import Path

from agent.tenancy import (
    RequestContext,
    TrustedClaimsContextMapper,
    TrustedIdentityError,
)
from mcp_server.auth.jwt import VerifiedJWT


class AgentAPIIdentityError(RuntimeError):
    """JWT 已验证，但身份 claims 无法形成安全 RequestContext。"""


class AgentIdentityMapper:
    """把 VerifiedJWT 通过共享 Claims Mapper 投影成 RequestContext。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.mapper = TrustedClaimsContextMapper(self.root)

    def map(self, verified: VerifiedJWT) -> RequestContext:
        """只传 subject/scopes/claims；Bearer Token 永远不会进入共享 Mapper。"""

        try:
            return self.mapper.map(
                subject=verified.subject,
                scopes=verified.scopes,
                claims=dict(verified.claims),
            )
        except TrustedIdentityError as exc:
            # 保留 Agent API 已有异常 Contract，不把协议层调用方耦合到 Agent Core 异常名。
            raise AgentAPIIdentityError(str(exc)) from exc
