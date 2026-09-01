"""Multi-tenant Request Context and authorization.

``GovernedRequestScopeEnforcer`` 依赖 ``agent.semantic_query``。
这里不能在 package import 阶段立即导入它，否则会形成：

    agent.tenancy
      -> semantic_scope
      -> agent.semantic_query
      -> executor
      -> agent.tenancy

因此保留公开 API，但对 Scope Enforcer 使用惰性导入。
"""

from .authorizer import GovernedRequestAuthorizer
from .context import bind_request_context, current_request_context
from .contracts import AuthorizationDecision, DimensionScope, RequestContext

__all__ = [
    "AuthorizationDecision",
    "DimensionScope",
    "RequestContext",
    "GovernedRequestAuthorizer",
    "GovernedRequestScopeEnforcer",
    "bind_request_context",
    "current_request_context",
]


def __getattr__(name: str):
    """按需加载依赖 Semantic Query 的 Scope Enforcer，避免 package 循环导入。"""

    if name == "GovernedRequestScopeEnforcer":
        from .semantic_scope import GovernedRequestScopeEnforcer

        return GovernedRequestScopeEnforcer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
