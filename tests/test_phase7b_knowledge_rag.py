from __future__ import annotations

from pathlib import Path

from agent.knowledge.chunking import KnowledgeChunker
from agent.knowledge.corpus import KnowledgeCorpus
from agent.knowledge.indexer import KnowledgeIndexer
from agent.knowledge.retrieval import GovernedKnowledgeRetriever

ROOT = Path(__file__).resolve().parents[1]


class FakeEmbeddings:
    dimensions = 4
    def embed(self, texts):
        return [[float(len(text) % 7), 1.0, 0.5, 0.25] for text in texts]


class FakeStore:
    def __init__(self):
        self.items = {}
    def ensure_collection(self, *, dimensions):
        assert dimensions == 4
    def upsert(self, chunks, vectors):
        for c, v in zip(chunks, vectors, strict=True):
            self.items[c.chunk_id] = {**{
                'chunk_id': c.chunk_id, 'document_id': c.document_id, 'title': c.title,
                'section': c.section, 'scope': c.scope, 'domain': c.domain,
                'authority': c.authority, 'source_path': c.source_path, 'tags': list(c.tags),
                'content': c.content, 'content_sha256': c.content_sha256,
                'document_sha256': c.document_sha256,
            }, '_vector': v}
    def count(self): return len(self.items)
    def search(self, vector, *, limit, minimum_score, scopes=None, domain=None, authorities=None):
        from agent.knowledge.qdrant_store import DenseHit
        rows=[]
        for payload in self.items.values():
            if scopes and payload['scope'] not in scopes: continue
            if domain and payload['domain'] != domain: continue
            if authorities and payload['authority'] not in authorities: continue
            rows.append(DenseHit(payload['chunk_id'], 0.9, {k:v for k,v in payload.items() if k!='_vector'}))
        return rows[:limit]
    def fetch(self, chunk_id):
        payload=self.items.get(chunk_id)
        return None if payload is None else {k:v for k,v in payload.items() if k!='_vector'}


def test_manifest_corpus_and_front_matter_are_consistent():
    docs = KnowledgeCorpus(ROOT).load()
    assert len(docs) == 18
    assert len({d.document_id for d in docs}) == len(docs)
    assert all(d.source_path.as_posix().startswith('knowledge/') for d in docs)


def test_structure_aware_chunk_ids_and_hashes_are_stable():
    docs = KnowledgeCorpus(ROOT).load()
    chunks1 = KnowledgeChunker().chunk_documents(docs)
    chunks2 = KnowledgeChunker().chunk_documents(docs)
    assert chunks1
    assert [c.chunk_id for c in chunks1] == [c.chunk_id for c in chunks2]
    assert [c.point_id for c in chunks1] == [c.point_id for c in chunks2]
    assert all('#c' in c.chunk_id and len(c.content_sha256) == 64 for c in chunks1)


def test_indexer_requeries_point_count_and_static_fake_never_writes_runtime_evidence(tmp_path):
    store=FakeStore(); provider=FakeEmbeddings()
    indexer=KnowledgeIndexer(ROOT, embedding_provider=provider, store=store)
    result=indexer.index(require_runtime_gate=False)
    assert result['runtime_verified'] is False
    assert result['status'] == 'KNOWLEDGE_INDEX_TEST_EXECUTION'
    assert result['evidence_scope'] == 'INJECTED_STATIC_TEST'
    assert store.count() == result['chunk_count']
    assert not (ROOT/'.runtime/evidence/phase7b/knowledge_index.json').exists()


def test_governed_search_filters_scope_and_fetches_exact_chunk(monkeypatch):
    store=FakeStore(); provider=FakeEmbeddings()
    KnowledgeIndexer(ROOT, embedding_provider=provider, store=store).index(require_runtime_gate=False)
    retriever=GovernedKnowledgeRetriever(ROOT, embedding_provider=provider, store=store)
    hits=retriever.search('dbt failure', scopes=['runbook'], top_k=3, require_runtime_gate=False)
    assert hits and all(h.scope == 'runbook' for h in hits)
    payload=retriever.fetch(hits[0].chunk_id, require_runtime_gate=False)
    assert payload['chunk_id'] == hits[0].chunk_id
    assert payload['evidence'] == 'RETRIEVED_KNOWLEDGE'
    assert payload['runtime_observed'] is False


def test_unknown_scope_is_rejected():
    retriever=GovernedKnowledgeRetriever(ROOT, embedding_provider=FakeEmbeddings(), store=FakeStore())
    try:
        retriever.search('x', scopes=['secrets'], require_runtime_gate=False)
    except ValueError as exc:
        assert 'Unknown knowledge scope' in str(exc)
    else:
        raise AssertionError('unknown scope must be rejected')
