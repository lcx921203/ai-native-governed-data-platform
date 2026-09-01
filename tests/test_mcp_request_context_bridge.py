"""MCP Principal -> shared RequestContext 的安全桥接回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.api.auth import AgentIdentityMapper
from agent.tenancy import (
    DimensionScope,
    RequestContext,
    TrustedClaimsContextMapper,
    current_request_context,
)
from mcp_server.auth.jwt import VerifiedJWT
from mcp_server.auth.scopes import (
    KNOWLEDGE_READ,
    MCP_BASE_READ,
    SEMANTIC_READ,
)
from mcp_server.registry import (
    GovernedMCPRegistry,
    MCPAuthorizationError,
    MCPPrincipal,
)


ROOT = Path(__file__).resolve().parents[1]


def _claims(**overrides):
    """构造 HTTP Agent API 与 MCP 都可消费的可信 claims。"""

    payload = {
        "sub": "user-1",
        "tenant_id": "tenant-west",
        "roles": ["analyst"],
        "allowed_metrics": [
            "gross_sales",
            "order_count",
        ],
        "allowed_datasets": ["orders"],
        "allowed_entities": ["*"],
        "allowed_dimensions": [
            "store__region",
        ],
        "allowed_knowledge_scopes": [
            "architecture",
        ],
        "dimension_scopes": {
            "store__region": "West",
        },
    }
    payload.update(overrides)
    return payload


def _context(
    *,
    allowed_metrics=frozenset(
        {"gross_sales"}
    ),
    allowed_dimensions=frozenset(
        {"store__region"}
    ),
    allowed_knowledge_scopes=frozenset(
        {"architecture"}
    ),
):
    """构造远程 MCP 使用的显式非 Local RequestContext。"""

    scopes = frozenset(
        {
            MCP_BASE_READ,
            SEMANTIC_READ,
            KNOWLEDGE_READ,
        }
    )
    return RequestContext(
        tenant_id="tenant-west",
        subject="user-1",
        scopes=scopes,
        allowed_metrics=allowed_metrics,
        allowed_datasets=frozenset(
            {"orders"}
        ),
        allowed_entities=frozenset(
            {"*"}
        ),
        allowed_dimensions=allowed_dimensions,
        allowed_knowledge_scopes=allowed_knowledge_scopes,
        dimension_scopes=(
            DimensionScope(
                "store__region",
                ("West",),
            ),
        ),
        implicit_local=False,
    )


def _principal(context: RequestContext):
    """把同一个 RequestContext 放进 MCPPrincipal。"""

    return MCPPrincipal(
        subject=context.subject,
        scopes=context.scopes,
        request_context=context,
    )


def test_agent_api_and_mcp_share_the_same_claim_mapping_contract():
    """同一份 Verified Claims 在 API 与 MCP 必须得到相同 RequestContext。"""

    claims = _claims()
    verified = VerifiedJWT(
        subject="user-1",
        client_id="client-1",
        scopes=(
            MCP_BASE_READ,
            SEMANTIC_READ,
        ),
        expires_at=1893456000,
        claims=claims,
    )

    api_context = AgentIdentityMapper(
        ROOT
    ).map(verified)
    shared_context = TrustedClaimsContextMapper(
        ROOT
    ).map(
        subject=verified.subject,
        scopes=verified.scopes,
        claims=verified.claims,
    )

    assert (
        api_context.to_dict()
        == shared_context.to_dict()
    )
    assert (
        api_context.tenant_id
        == "tenant-west"
    )
    assert (
        api_context.dimension_scopes[0].values
        == ("West",)
    )


def test_mcp_capability_scope_without_request_context_fails_closed():
    """即使 OAuth Scope 足够，缺 Trusted RequestContext 也不能回退到旧 capability-only 模式。"""

    registry = GovernedMCPRegistry(
        ROOT,
        profile="analyst",
    )
    principal = MCPPrincipal(
        subject="user-1",
        scopes=frozenset(
            {
                MCP_BASE_READ,
                SEMANTIC_READ,
            }
        ),
        request_context=None,
    )

    with pytest.raises(
        MCPAuthorizationError,
        match="Trusted RequestContext",
    ):
        registry.dispatch(
            "query_semantic_metric",
            {
                "metric": "gross_sales",
                "question": "gross_sales 是多少？",
            },
            principal,
        )


def test_mcp_metric_object_allowlist_blocks_before_semantic_execution(
    monkeypatch,
):
    """MCP Metric 不在 allowlist 时必须在调用 Semantic Tool 前拒绝。"""

    registry = GovernedMCPRegistry(
        ROOT,
        profile="analyst",
    )
    called = {"value": False}

    def fake_query(*_args, **_kwargs):
        called["value"] = True
        return {
            "status": "ANSWERED",
            "evidence": "RUNTIME_VERIFIED",
            "payload": {},
        }

    monkeypatch.setattr(
        "mcp_server.registry.query_semantic_metric",
        fake_query,
    )

    context = _context(
        allowed_metrics=frozenset(
            {"order_count"}
        )
    )
    with pytest.raises(
        MCPAuthorizationError,
        match="allowed metric scope",
    ):
        registry.dispatch(
            "query_semantic_metric",
            {
                "metric": "gross_sales",
                "question": "gross_sales 是多少？",
            },
            _principal(context),
        )

    assert called["value"] is False


def test_mcp_semantic_dispatch_binds_shared_contextvar(
    monkeypatch,
):
    """MCP Semantic Tool 执行时必须能从 ContextVar 读取 tenant/dimension scope。"""

    registry = GovernedMCPRegistry(
        ROOT,
        profile="analyst",
    )
    observed = {}

    def fake_query(*_args, **_kwargs):
        active = current_request_context()
        observed["tenant_id"] = (
            active.tenant_id
            if active
            else ""
        )
        observed["dimension_scopes"] = (
            active.dimension_scopes
            if active
            else ()
        )
        return {
            "status": "ANSWERED",
            "evidence": "STATIC_CONTRACT",
            "payload": {"ok": True},
        }

    monkeypatch.setattr(
        "mcp_server.registry.query_semantic_metric",
        fake_query,
    )

    context = _context()
    result = registry.dispatch(
        "query_semantic_metric",
        {
            "metric": "gross_sales",
            "question": "gross_sales 是多少？",
        },
        _principal(context),
    )

    assert result.status == "ANSWERED"
    assert (
        observed["tenant_id"]
        == "tenant-west"
    )
    assert (
        observed["dimension_scopes"][0].dimension
        == "store__region"
    )
    assert (
        observed["dimension_scopes"][0].values
        == ("West",)
    )
    assert current_request_context() is None


class _ScopedKnowledge:
    """记录 Registry 是否自动注入 RequestContext Knowledge Scope。"""

    def __init__(self):
        self.received_scopes = None

    def search_knowledge(
        self,
        **kwargs,
    ):
        self.received_scopes = kwargs.get(
            "scopes"
        )
        return {
            "tool": "search_knowledge",
            "status": "ANSWERED",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": {
                "results": [
                    {
                        "chunk_id": "doc#c0001",
                        "scope": "architecture",
                    }
                ]
            },
            "warnings": [],
            "sources": [],
        }

    def fetch_knowledge(
        self,
        **_kwargs,
    ):
        return {
            "tool": "fetch_knowledge",
            "status": "ANSWERED",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": {
                "chunk_id": "doc#c0001",
                "scope": "internal",
                "content": "must not escape",
            },
            "warnings": [],
            "sources": [],
        }


def test_mcp_knowledge_search_injects_allowed_scope_when_caller_omits_it():
    """受限身份未传 scopes 时，Registry 不能让 Retriever 默认搜索全部语料。"""

    knowledge = _ScopedKnowledge()
    registry = GovernedMCPRegistry(
        ROOT,
        profile="analyst",
        knowledge_tools=knowledge,
    )
    context = _context()

    registry.dispatch(
        "search_knowledge",
        {
            "query": "架构设计",
            "scopes": None,
        },
        _principal(context),
    )

    assert (
        knowledge.received_scopes
        == ["architecture"]
    )


def test_mcp_fetch_knowledge_post_filters_chunk_scope():
    """exact chunk_id 读取后仍必须校验 payload.scope，避免跨 Knowledge Scope 返回正文。"""

    knowledge = _ScopedKnowledge()
    registry = GovernedMCPRegistry(
        ROOT,
        profile="analyst",
        knowledge_tools=knowledge,
    )
    context = _context()

    with pytest.raises(
        MCPAuthorizationError,
        match="allowed knowledge scope",
    ):
        registry.dispatch(
            "fetch_knowledge",
            {"chunk_id": "doc#c0001"},
            _principal(context),
        )


def test_semantic_scope_enforcer_blocks_disallowed_business_dimension():
    """ContextVar 注入之外，Semantic Plan 本身也不能使用未授权 Business Dimension。"""

    from agent.semantic_query import (
        GovernedSemanticQueryPlanner,
    )
    from agent.tenancy import (
        GovernedRequestScopeEnforcer,
    )

    plan = GovernedSemanticQueryPlanner(
        ROOT
    ).plan(
        metric="gross_sales",
        question=(
            "2026-08-05 美国西部地区 "
            "gross_sales 是多少？"
        ),
    )
    context = _context(
        allowed_dimensions=frozenset(
            {"item__brand"}
        )
    )

    scoped, warning = (
        GovernedRequestScopeEnforcer(
            ROOT
        ).apply(
            plan,
            context,
        )
    )

    assert scoped is plan
    assert (
        "outside allowed dimension scope"
        in warning
    )
