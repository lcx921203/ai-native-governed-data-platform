# Agent → Knowledge RAG and Multi-format Document Ingestion

Current canonical status: **SOURCE / STATIC CLOSED; REAL RUNTIME DEFERRED**.

## 1. Agent does use Knowledge RAG

The current deterministic route is explicit:

```text
User question
  ↓
DeterministicToolRouter
  ├─ Dataset Runtime → Dagster authority
  ├─ Metric Definition / Value → MetricFlow authority
  └─ Why / Design / SOP / Runbook → KNOWLEDGE_QUERY
                                      ↓
                               search_knowledge
                                      ↓
                              exact chunk_id only
                                      ↓
                               fetch_knowledge
                                      ↓
                                Claim Ledger
                                      ↓
                                 LLM renderer
```

The important boundary is **Structured Truth First; RAG Second**. A phrase such as `为什么` does not automatically turn a question into RAG. The router first checks whether a governed Dataset Runtime or Metric Definition target owns the question.

Knowledge claims enter the Claim Ledger as `KNOWLEDGE_EVIDENCE` with evidence level `RETRIEVED_KNOWLEDGE`. They are never promoted to `RUNTIME_VERIFIED` merely because retrieval succeeded.

If the Phase 7B index evidence is absent, `search_knowledge` returns `DEFERRED`; it does not silently return `NOT_FOUND` and it does not infer an answer from source files.

## 2. Current corpus versus supported formats

Current active Manifest corpus:

```text
18 active documents
source format: Markdown only
```

That is the current data fact.

The **source-defined ingestion capability** is now broader:

```text
Markdown ─┐
PDF      ─┼→ Parser Registry → KnowledgeDocument / KnowledgeBlock → Governance → Chunking
DOCX     ─┘
```

### Markdown

- UTF-8 text;
- YAML Front Matter required;
- Manifest id/scope must match Front Matter;
- existing Markdown chunk identity remains stable.

### Text-layer PDF

- parser: `pypdf.PdfReader`;
- page number is retained in `KnowledgeBlock.page_number` and chunk provenance;
- empty/image-only PDF fails closed;
- OCR / layout extraction is **DEFERRED**.

### DOCX

- parser: `python-docx`;
- document order preserves Heading / Paragraph / Table blocks;
- Heading updates the current section path;
- tables are normalized to stable row text before chunking.

PDF / DOCX do not carry the project's Markdown YAML Front Matter contract, so identity / title / scope / domain / authority / owner / status must be complete in `corpus_manifest.yml` before they may enter the governed corpus.

## 3. Runtime boundary

The following are **not** claimed by this source change:

- real enterprise PDF/DOCX documents have been ingested;
- scanned PDF OCR has run;
- OpenAI Embedding has run;
- Qdrant has been indexed;
- Cohere Reranker has run;
- Agent Knowledge Runtime has returned a production answer.

Those remain `NOT EXECUTED` / `DEFERRED` until workstation Runtime Evidence exists.
