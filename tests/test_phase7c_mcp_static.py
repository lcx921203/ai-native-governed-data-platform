from __future__ import annotations

from pathlib import Path

import pytest

from agent.tenancy import RequestContext
from mcp_server.auth.profiles import PROFILES
from mcp_server.auth.scopes import (
    KNOWLEDGE_READ,
    MCP_BASE_READ,
    METADATA_READ,
    SEMANTIC_READ,
)
from mcp_server.prompts import (
    explain_metric,
    investigate_metric_issue,
)
from mcp_server.registry import (
    GovernedMCPRegistry,
    MCPAuthorizationError,
    MCPPrincipal,
)
from mcp_server.resources import read_dataset_resource


ROOT = Path(__file__).resolve().parents[1]


class FakeKnowledge:
    def search_knowledge(self, **kwargs):
        scopes = list(kwargs.get("scopes") or [])
        return {
            "tool": "search_knowledge",
            "status": "ANSWERED",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": {
                "results": [
                    {
                        "chunk_id": "x#c0001",
                        "scope": scope,
                    }
                    for scope in scopes
                ]
            },
            "warnings": [],
            "sources": [],
        }

    def fetch_knowledge(self, **kwargs):
        return {
            "tool": "fetch_knowledge",
            "status": "NOT_FOUND",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": {},
            "warnings": [],
            "sources": [],
        }


def principal(*scopes):
    frozen = frozenset(scopes)
    context = RequestContext(
        tenant_id="test",
        subject="tester",
        scopes=frozen,
    )
    return MCPPrincipal(
        "tester",
        frozen,
        context,
    )


def test_profiles_only_register_read_only_governed_surface():
    forbidden = {
        "execute_sql",
        "shell",
        "python",
        "datahub_write",
        "dagster_launch_run",
        "dagster_backfill",
        "dagster_recovery",
        "knowledge_reindex",
    }
    for tools in PROFILES.values():
        assert not (
            set(tools)
            & forbidden
        )


def test_profile_and_scope_are_both_required():
    registry = GovernedMCPRegistry(
        ROOT,
        profile="knowledge_only",
        knowledge_tools=FakeKnowledge(),
    )
    with pytest.raises(
        MCPAuthorizationError
    ):
        registry.dispatch(
            "search_knowledge",
            {"query": "x"},
            principal(MCP_BASE_READ),
        )

    result = registry.dispatch(
        "search_knowledge",
        {
            "query": "x",
            "scopes": ["architecture"],
        },
        principal(
            MCP_BASE_READ,
            KNOWLEDGE_READ,
        ),
    )
    assert (
        result.evidence
        == "RETRIEVED_KNOWLEDGE"
    )

    with pytest.raises(
        MCPAuthorizationError
    ):
        registry.dispatch(
            "get_dataset_context",
            {"dataset": "orders"},
            principal(
                MCP_BASE_READ,
                METADATA_READ,
            ),
        )


def test_metadata_resource_routes_through_registry_not_file_read():
    registry = GovernedMCPRegistry(
        ROOT,
        profile="analyst",
        knowledge_tools=FakeKnowledge(),
    )
    payload = read_dataset_resource(
        registry,
        "orders",
        principal(
            MCP_BASE_READ,
            METADATA_READ,
        ),
    )
    assert (
        payload["tool"]
        == "get_dataset_context"
    )

    with pytest.raises(ValueError):
        read_dataset_resource(
            registry,
            "../secrets",
            principal(
                MCP_BASE_READ,
                METADATA_READ,
            ),
        )


def test_prompts_are_templates_and_do_not_execute_tools():
    text = explain_metric(
        "gross_sales"
    )
    assert (
        "MetricFlow" in text
        and "invent runtime" in text
    )
    issue = investigate_metric_issue(
        "gross_sales",
        "2026-08-18",
    )
    assert (
        "Dagster" in issue
        and "no recovery" in issue
    )


def test_semantic_tool_stays_governed_and_gate_closed_by_default(
    monkeypatch,
):
    registry = GovernedMCPRegistry(
        ROOT,
        profile="analyst",
        knowledge_tools=FakeKnowledge(),
    )
    result = registry.dispatch(
        "query_semantic_metric",
        {
            "metric": "gross_sales",
            "question": "昨天 gross_sales",
        },
        principal(
            MCP_BASE_READ,
            SEMANTIC_READ,
        ),
    )
    assert result.status in {
        "DEFERRED",
        "BLOCKED",
        "CLARIFICATION_REQUIRED",
        "ERROR",
    }
    assert (
        result.evidence
        != "RUNTIME_VERIFIED"
    )


def test_mcp_server_source_uses_v2_sdk_and_stateless_http_security():
    text = (
        ROOT
        / "mcp_server/server.py"
    ).read_text()
    assert (
        "from mcp.server import MCPServer"
        in text
    )
    assert "stateless_http=True" in text
    assert "TransportSecuritySettings" in text
    assert "get_access_token" in text
    assert "TrustedClaimsContextMapper" in text
    assert "datahub_write" not in text.lower()
