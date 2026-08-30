"""Agent Core → Knowledge RAG 显式路由契约测试。"""

from __future__ import annotations

from pathlib import Path

from agent.response.composer import GovernedResponseComposer
from agent.router import DeterministicToolRouter, GovernedPlanExecutor, Intent

ROOT = Path(__file__).resolve().parents[1]


class FakeKnowledgeTools:
    """不启动 Qdrant 的静态 Fake，只验证 Router / Executor 的调用顺序和证据边界。"""

    def search_knowledge(self, **arguments):
        """返回两个固定 governed chunk 候选。"""
        return {
            "tool": "search_knowledge",
            "status": "ANSWERED",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": {
                "results": [
                    {
                        "chunk_id": "commerce.architecture.platform#c0001",
                        "document_id": "commerce.architecture.platform",
                        "title": "Platform Architecture",
                        "section": "Ingestion",
                        "scope": "architecture",
                    },
                    {
                        "chunk_id": "commerce.architecture.authority_boundaries#c0001",
                        "document_id": "commerce.architecture.authority_boundaries",
                        "title": "Authority Boundaries",
                        "section": "Boundaries",
                        "scope": "architecture",
                    },
                ],
                "count": 2,
            },
            "warnings": [],
            "sources": [
                {
                    "kind": "knowledge_chunk",
                    "location": "commerce.architecture.platform#c0001",
                    "source_path": "knowledge/architecture/platform_architecture.md",
                    "runtime_verified": False,
                }
            ],
        }

    def fetch_knowledge(self, *, chunk_id: str):
        """按 exact chunk_id 返回完整知识切片；不接受任意文件路径。"""
        return {
            "tool": "fetch_knowledge",
            "status": "ANSWERED",
            "evidence": "RETRIEVED_KNOWLEDGE",
            "payload": {
                "chunk_id": chunk_id,
                "document_id": chunk_id.split("#", 1)[0],
                "title": "Governed Knowledge",
                "section": "Design",
                "scope": "architecture",
                "domain": "commerce",
                "authority": "design_decision",
                "source_path": "knowledge/architecture/platform_architecture.md",
                "content": "当前设计优先保持直接 CDC → Iceberg；只有真实多消费者需求出现时才引入 Kafka 解耦。",
                "content_sha256": "a" * 64,
                "document_sha256": "b" * 64,
                "source_format": "markdown",
                "page_numbers": [],
                "evidence": "RETRIEVED_KNOWLEDGE",
                "runtime_observed": False,
            },
            "warnings": [],
            "sources": [
                {
                    "kind": "knowledge_chunk",
                    "location": chunk_id,
                    "source_path": "knowledge/architecture/platform_architecture.md",
                    "runtime_verified": False,
                }
            ],
        }


def test_explicit_design_question_routes_agent_core_to_knowledge_rag():
    router = DeterministicToolRouter(ROOT)
    plan = router.plan("为什么这样设计：MySQL CDC 不先经过 Kafka？")
    assert plan.intent is Intent.KNOWLEDGE_QUERY
    assert plan.status.value == "PLANNED"
    assert [step.tool for step in plan.steps] == ["search_knowledge", "fetch_top_knowledge_hits"]
    assert set(plan.steps[0].arguments["scopes"]) == {"architecture", "modeling", "governance", "business"}


def test_structured_authority_still_wins_before_rag():
    router = DeterministicToolRouter(ROOT)
    assert router.plan("为什么 orders 昨天没更新？").intent is Intent.RUNTIME_DIAGNOSIS
    assert router.plan("gross_sales 怎么算？").intent is Intent.METRIC_DEFINITION


def test_knowledge_execution_is_search_then_exact_fetch_and_claim_ledger_stays_non_runtime():
    router = DeterministicToolRouter(ROOT)
    plan = router.plan("为什么这样设计：MySQL CDC 不先经过 Kafka？")
    execution = GovernedPlanExecutor(ROOT, knowledge_tools=FakeKnowledgeTools()).execute(plan)
    assert execution.status.value == "COMPLETE"
    assert [item["tool"] for item in execution.results] == [
        "search_knowledge",
        "fetch_knowledge",
        "fetch_knowledge",
    ]
    assert all(item["evidence"] == "RETRIEVED_KNOWLEDGE" for item in execution.results)

    envelope = GovernedResponseComposer(ROOT).compose(execution)
    knowledge_claims = [claim for claim in envelope.claims if claim.kind.value == "KNOWLEDGE_EVIDENCE"]
    assert knowledge_claims
    assert all(claim.evidence == "RETRIEVED_KNOWLEDGE" for claim in knowledge_claims)
    assert all(claim.runtime_observed is False for claim in knowledge_claims)
    assert "RUNTIME_VERIFIED" not in envelope.evidence_levels


def test_real_knowledge_route_defers_cleanly_without_runtime_index_evidence(monkeypatch):
    """没有 Phase 7B Runtime evidence 时，Agent 返回 DEFERRED 而不是崩溃或假装 NOT_FOUND。"""
    monkeypatch.setenv("PHASE7B_ALLOW_KNOWLEDGE_RETRIEVAL", "false")
    router = DeterministicToolRouter(ROOT)
    execution = GovernedPlanExecutor(ROOT).execute(router.plan("为什么这样设计：MySQL CDC 不先经过 Kafka？"))
    assert execution.status.value == "DEFERRED"
    assert execution.results[-1]["tool"] == "search_knowledge"
    assert execution.results[-1]["evidence"] == "DEFERRED"
