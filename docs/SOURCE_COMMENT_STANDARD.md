# Source Comment Standard — AI-Native Governed Data Platform & Data Agent

This document defines the **current canonical source** commenting rule used by the engineering project and the technical blog.
Historical milestone ZIPs remain historical evidence, but they do **not** make the current canonical source immutable. When the current source evolves, comments and implementation may be changed together and a new complete ZIP / SHA-256 is produced.

## 1. Six layers of explanation

Comments should help a reader understand the code in the order that matters to engineering work:

1. **Business / code logic（业务与代码逻辑）** — what problem this unit solves and how the decision flows.
2. **Language syntax（语言语法）** — explain Python / SQL / Flink / Jinja / GraphQL syntax only when it is meaningful in this project.
3. **Input / output（输入输出）** — what shape enters and what shape leaves.
4. **Data semantics（数据语义）** — Grain, Business Time, source clock, state, event, version, cardinality, etc.
5. **Framework / API knowledge（框架与 API）** — explain the Dagster / Flink / dbt / MetricFlow / DataHub / GraphQL API used at that exact point.
6. **Engineering boundary（工程边界）** — why the code is designed this way, what it intentionally does not prove or do, and which failure state must fail closed.

The goal is **not** to turn source files into beginner textbooks. Explain what is necessary to understand the current implementation and production boundary.

## 2. Python

Every function / method that carries an independent engineering responsibility must have a Chinese-first docstring close to the function itself.
Core engineering modules and important classes that appear in the blog/source surface must also use Chinese-first explanatory docstrings; professional API / class / field names remain English.
The docstring should normally cover: responsibility, key input/output, important framework API or syntax, and engineering boundary.

Example:

```python
def collect_partition_recovery_state(...):
    """收集一个业务分区的恢复运行事实。

    输入：DagsterInstance 与 partition_key。
    输出：RecoveryRuntimeState；本函数只读，不触发恢复。
    Dagster API：通过 exact asset partition materialization 查询判断具体分区是否完成。
    工程边界：Run SUCCESS 不自动等于 9/9 Mart exact partition complete。
    """
```

`tuple[str, ...]` 中的 `...` 是真实 Python typing 语法，表示“任意长度、元素均为 str 的 tuple”，不是博客省略号。

## 3. SQL / dbt

A SQL model has no function boundary, so comments belong near the **model contract and important CTEs**.
For important CTEs explain:

- why the CTE exists;
- its input Grain;
- whether the execution window is used for **change discovery** or **complete-context reads**;
- its output Grain;
- why a join does not create Fanout;
- what belongs to dbt Source / Staging / Intermediate / Mart instead of being mixed together.

## 4. Flink SQL / PyFlink

Explain Source Changelog, Event Time, Watermark, State, TTL, Side Output, Checkpoint and Sink semantics at the point where those APIs are used.
`Exactly-once` in source code means the topology/configuration is **defined for exactly-once**; it is not Runtime PASS until a real failure-recovery drill produces evidence.

## 5. GraphQL

Each important query / pagination query should explain:

- query variables;
- returned data shape;
- which fields are Array vs Connection;
- ownership of `after` / `endCursor`;
- Business Time fields when relevant;
- the fail-closed boundary for missing cursor / schema drift.

## 6. YAML / Semantic contracts

Comments should explain the meaning of the contract, not merely translate field names.
Examples include dbt `source()`, MetricFlow `entity.type`, `agg_time_dimension`, governed Metric formulas, DataHub identity/ownership/lineage aspects, and runtime gate policy fields.

## 6A. DataHub metadata / governance

For DataHub code and contracts, explain exact Dataset identity, Dataset URN shape, Domain / Ownership / Tags / Glossary Terms / Structured Properties, lineage hop limits, and the difference between static governance expectation and live re-query evidence.

Key boundary:

- expected Dataset URN is not a resolved identity;
- `REFERENCE_ONLY` is not `SEMANTIC_READY`;
- `SEMANTIC_READY` is not `RUNTIME_VERIFIED`;
- Agent metadata reads use exact URN + bounded lineage and never expose arbitrary DataHub graph mutation/query surfaces.

## 6C. Knowledge RAG / Qdrant / Reranker

For governed knowledge code, comments must explain the difference between **source knowledge**, **generated vector index**, **retrieval evidence**, and **runtime truth**:

- Corpus Manifest + Front Matter decide which documents may enter the knowledge plane; Agent cannot supply an arbitrary file path.
- Structure-aware Chunking should explain heading/paragraph/code-block boundaries, stable `chunk_id`, and content/document SHA provenance.
- Embedding / Qdrant comments should explain vector dimensions, COSINE search, payload filtering, stable Point IDs, and why index upsert/re-query is not implied Runtime PASS.
- Two-stage retrieval must distinguish Dense Retrieval from Reranking; `DENSE_FALLBACK` is an explicit degraded mode, not a successful rerank.
- `search_knowledge` returns candidates/provenance; `fetch_knowledge` requires an exact governed `chunk_id` and never becomes arbitrary file read.
- Retrieval evaluation should explain Recall / MRR / NDCG and the difference between a Golden Case definition and a live Runtime evidence file.
- `RETRIEVED_KNOWLEDGE` is not `RUNTIME_VERIFIED`; RAG may explain Why / Design / SOP / Troubleshooting, but it may not create MetricFlow numeric values, DataHub ownership, or Dagster runtime facts.
- Real OpenAI Embedding / Qdrant / Cohere Reranker success remains DEFERRED until the Runtime gate and evidence contracts are actually executed.

## 6D. Serving Layer / Trino / BI / API

Serving code must preserve the split between **metric authority** and **consumer projection**:

- Serving Contract may reference governed MetricFlow metrics/dimensions but must not reimplement formulas in SQL/Python.
- Dagster owns export timing/partition responsibility; Schedule is not consumer Freshness proof.
- Iceberg Serving tables are rebuildable projections/caches, not new Business Truth.
- Trino is a read/query serving engine over Iceberg; it does not own dbt modeling or metric definitions.
- FastAPI endpoints are fixed business contracts. Do not expose arbitrary SQL, caller-defined table names, or a second dynamic metric language.
- API/BI and Agent may have different serving paths, but the metric names and business semantics must still originate from the same MetricFlow authority.
- Runtime materialization must fail before write when MetricFlow output schema, partition day, or key contract is invalid.

## 7. Blog rendering

The blog should display **real current-source code**. It may add clearly marked `[博客阅读注释]` for presentation, but the important method/CTE/API explanations should first exist in the current canonical source whenever the format supports comments.

Blog code reading continues to use the same six layers and must preserve the engineering evidence boundary:

- SOURCE / STATIC PASS is not Runtime PASS;
- DEFINED / DEFERRED / NOT EXECUTED are not rewritten as executed;
- APPROVED is not EXECUTED.

## 6B. Governed Agent analysis / diagnostics

For Router, Semantic Query, Clarification, Analysis Session, Comparison, Anomaly, Driver Attribution,
Incident Response, Approval and Claim Ledger code, comments must make the authority boundary explicit:

- Deterministic Router plans tools; it does not invent an unverified business target.
- Semantic Query Planner builds bounded MetricFlow plans; it never opens arbitrary SQL / raw `WHERE`.
- Clarification / Session state must explain checksum / fingerprint integrity when used.
- Derived comparison / anomaly / attribution numbers require the evidence levels defined by the current contract;
  static/fake results do not become runtime facts.
- Operational Health reads Dagster exact-partition truth and does not own Recovery execution.
- Driver Attribution is an analytical lens, not causal proof; independent lens contributions are not additive.
- Incident Response is advisory; Approval owns approval state only.
- `APPROVED` is not `EXECUTED`; external execution must re-read current evidence and remains outside Agent authority.
- Claim Ledger owns the evidence boundary; LLM only renders approved claims and limitations.
