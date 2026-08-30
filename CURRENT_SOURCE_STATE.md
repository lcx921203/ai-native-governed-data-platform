# Commerce Modern Data Platform — Current Source State

Date: 2026-08-21

## Current engineering source authority

This directory is the consolidated source baseline built from three auditable layers:

1. **Original Phase 6 canonical base**
   - archive: `commerce-modern-data-platform-learning-phase6-static-closure.zip`
   - historical SHA-256: `96c7c525eb1d90e9171b675d2a02dfa7cf84917e0632b2c9c04a771d67e6e7bb`
   - the historical archive is not rewritten; its identity remains immutable.

2. **Phase 7 final-handoff canonical overlay**
   - archive SHA-256: `835ceaa61a1e89b2a5f85a813a0f539cb616b71936d6b5b70c6b6e1b04d9f8e1`
   - 54 physical overlay files were copied byte-for-byte and re-verified against the handoff package.

3. **Post-Phase6 corrections and Phase 7 source-completion files**
   - two legacy runtime scripts had an invalid byte before their shebang and were corrected;
   - Spark Ivy cache persistence and Phase 7 runtime-gate defaults were retained;
   - Phase 7A historical source recovered directly from prior project artifacts was restored;
   - physical Phase 7B RAG/Qdrant, Phase 7C MCP, DataHub live cutover wrappers, and source-closure runners that were described by the final handoff but not physically present in the overlay were reconstructed with explicit provenance.


## 2026-08-20 Shopify ingestion enhancement

After the 2026-08-19 consolidated baseline, the project source was intentionally evolved for the blog/interview learning path without rewriting historical provenance:

- added `SHOPIFY_SOURCE_MODE` with `fixture` / `production` source modes;
- 不再使用单独的数据源 YAML 开关；真实密钥仍然只放环境变量；
- wired the production profile into Dagster Raw ingestion;
- production extraction now lands full Order JSONL under `.runtime/` and uses Spark only for Raw Iceberg append;
- implemented bounded HTTP/GraphQL retry handling;
- implemented Order root cursor pagination plus nested pagination for `Order.lineItems`, `Refund.refundLineItems`, `Refund.transactions`, `Fulfillment.fulfillmentLineItems`, and `Fulfillment.events`;
- removed optional truncation arguments from Order array fields (`transactions`, `refunds`, `fulfillments`) so the main query does not silently cap them at 100;
- added source/static tests for source-mode switching and cursor behavior;
- updated the latest blog snapshot with the production-source walkthrough and Python/GraphQL learning annotations.

This is a **post-baseline source enhancement**, not a claim that a real Shopify shop was contacted. Real credentialed Shopify API runtime evidence remains deferred.

## State

- Phase 1–6 engineering source: **CLOSED**
- Phase 7 engineering/source closure: **CLOSED**
- Phase 7A workstation/runtime bootstrap: **ENGINEERED; REAL RUNTIME DEFERRED**
- Phase 7B Knowledge RAG + Qdrant + Reranker: **ENGINEERED; REAL RUNTIME DEFERRED**
- Phase 7C Commerce MCP: **ENGINEERED; REAL RUNTIME DEFERRED**
- Phase 7D end-to-end runtime evidence: **ENGINEERED; REAL RUNTIME DEFERRED**

No Docker, Polaris, Spark, Flink, Kafka, MySQL CDC, DataHub, MetricFlow, Dagster, Qdrant, MCP OAuth, or OpenAI runtime success is inferred from static/source validation.

## Static/source acceptance

The consolidated source closure currently passes:

- full pytest suite: **413 passed**
- historical Phase 7A focused tests: **14 passed**
- Phase 7B focused tests: **8 passed**
- Phase 7C focused tests: **6 passed**
- Phase 7 source-integrity / live-cutover static tests: **12 passed**
- YAML/JSON parsing: **PASS**
- Python compilation: **PASS**
- all `infra/runtime/*.sh` syntax + first-byte shebang audit: **PASS**
- all Phase 7 live entrypoints default fail-closed: **PASS**
- `.runtime/` evidence present in source package: **NO**

Canonical static command:

```bash
bash infra/runtime/run_phase7_source_closure.sh
```

Real runtime remains a later workstation step and requires explicit per-capability gates.

## Blog / handoff preservation

To prevent another base-package handoff break, this baseline also carries:

- `blog/commerce-modern-data-platform-latest.html`
- `blog/commerce-modern-data-platform-v78-blog.html`
- `docs/handoff/phase7/` — the final Phase 7 handoff context and original checksum record.

The V78 warm-white HTML remains the visual reference; the latest HTML preserves the later content snapshot from the handoff.

## Provenance

See `FULL_SOURCE_PROVENANCE.csv` for file-level origin classification. Historical canonical bytes and reconstructed source are intentionally distinguished rather than presented as the same thing.


## 2026-08-20 user correction

- Removed the unnecessary Shopify source YAML switch introduced by speech-to-text misunderstanding.
- Fixture / Production remain two source-code branches selected by `SHOPIFY_SOURCE_MODE`.
- First-chapter Shopify Python comments are Chinese-first for project-based learning: logic, Python syntax, input/output shape, and engineering boundaries.


## 2026-08-20 multi-ingestion expansion

- Added production-oriented MySQL Item / Store integration: Flink CDC 3.6 + Flink SQL -> Iceberg v2 upsert current state.
- Added production-oriented behavior integration: FastAPI Collector -> Kafka -> PyFlink DataStream -> Iceberg.
- Behavior source explicitly implements Event Time, bounded Watermark, idle partition handling, Keyed State + TTL,
  5-minute window, Allowed Lateness, invalid / too-late Side Outputs, Exactly-once Checkpoint config, RocksDB state,
  Restart Strategy and failure-drill runbook.
- Added Shopify proactive GraphQL Query Cost throttling and X-Shopify-API-Version fail-closed guard.
- Added Schema Drift / Iceberg evolution policy: Raw evidence survives parser drift; safe structural evolution is distinct
  from business-semantic compatibility.
- Runtime evidence for MySQL/Kafka/Flink failure recovery remains DEFERRED until a real workstation drill is executed.

## 2026-08-20 order lifecycle accumulating snapshot

A new dbt business Mart has been added at:

`dbt/mercaso_dbt/models/marts/commerce/order_lifecycle_snapshot.sql`

Source contract:

- Grain: one row per `order_id`;
- materialization: incremental Iceberg `MERGE` by `order_id`;
- execution-window changes are propagated from Order, Transaction, Refund, Fulfillment, and FulfillmentEvent to `affected_order_ids`;
- affected Orders are recomputed from complete Canonical Current child rows, not only rows inside the current window;
- `first_paid_at` requires a successful `CAPTURE` / `SALE`; successful Authorization is tracked separately;
- explicit lifecycle milestones include order, payment, refund, fulfillment, in-transit, delivered, cancelled, and closed times;
- no `picked_at` is invented because the current source contract has no authoritative picking timestamp;
- a dbt singular test is defined for obvious lifecycle timestamp ordering violations.

Validation state for this enhancement:

- lifecycle source/static contract tests: **PASS**;
- dbt Core parse/build in this environment: **NOT EXECUTED** (`dbt` executable unavailable);
- Spark / Iceberg materialization: **NOT EXECUTED**;
- real accumulating-snapshot Runtime Evidence: **DEFERRED**.

The model carries the existing `shopify_windowed` tag and is therefore part of the source-defined daily dbt execution selection.
It is now promoted into the governed **nine-Mart** consumer Freshness / Recovery completeness SLA.
`SHOPIFY_DAILY_MART_ASSET_KEYS` includes `order_lifecycle_snapshot`, so exact-partition completeness, Freshness policy application, Recovery State, Recovery Sensor acceptance, and replay guards all require this Mart together with the previous eight. This is a **source/static contract promotion only**; no real Dagster daemon or nine-Mart Runtime completion is claimed.


### Post-baseline orchestration compatibility layer

The historical Phase 6 milestone archive still represents the original eight-Mart closure byte-for-byte.
The **current canonical tree is a later evolution**: it keeps the same recovery policy semantics where unchanged, adds the nine-Mart compatibility layer, and may also carry comment/docstring improvements. Historical identity is preserved by the old archive/checksum, not by forcing the current working copy to remain immutable.

Current orchestration includes:

- `orchestration/dagster/commerce_dagster/consumer_sla.py` — current nine-Mart SLA registry;
- `orchestration/dagster/commerce_dagster/recovery_state_current.py` — exact-partition reader bound to the nine-Mart registry;
- `freshness.py` and `sensors.py` consume the current registry/reader;
- Phase 3C acceptance harnesses now evaluate 8/9 incomplete and 9/9 complete scenarios;
- R01 same-run schedule evidence explicitly imports the current nine-Mart SLA registry;
- current Agent/Phase 7 runtime adapters use the nine-Mart state reader;
- Agent `automation_contract()` keeps the established Phase 6 timing semantics from `automation_policy.py` and reads current Mart membership from `consumer_sla.py`, so `order_lifecycle_snapshot` is correctly reported as daily-managed.

Downstream propagation added in the same source evolution:

- Iceberg write-layout DDL and pruning validator include `order_lifecycle_snapshot` using `order_time`;
- DataHub expected identity / governance projection includes the lifecycle dataset;
- as of **2026-08-30**, an explicit Order Lifecycle Conversion Metric Contract is approved: `order_lifecycle_snapshot` is now `semantic-enabled`, inherits `SEMANTIC_READY`, and exposes governed Order → Paid / Fulfillment / Delivered conversion metrics through MetricFlow.

Current canonical source evolution rule:

- historical Phase 6 / Phase 7 milestone ZIPs and their SHA-256 values remain immutable historical evidence;
- the **current canonical source tree is allowed to evolve**, including implementation, comments, tests and contracts;
- when a formerly Phase-6-origin file changes in the current tree, provenance is reclassified as a post-baseline current-source evolution instead of pretending it is still byte-for-byte Phase 6;
- the current Phase 6 closure lock is explicitly re-closed/regenerated when one of its tracked files intentionally changes; this does not rewrite the historical Phase 6 archive.

Global source-comment standard:

- Chapter 01–04 core Python functions/methods now carry local Chinese-first docstrings;
- SQL/dbt uses model + important CTE comments; GraphQL uses query/pagination comments; YAML explains contract/API semantics; Flink comments explain Watermark/State/Checkpoint/Exactly-once boundaries;
- comments follow six layers: business logic, language syntax, input/output, data semantics, framework/API knowledge, and engineering boundary;
- `tests/test_source_comment_contract.py` prevents the core source surface from regressing to file-header-only documentation.
- detailed rules: `docs/SOURCE_COMMENT_STANDARD.md`.


## 2026-08-21 Chapter 05 DataHub source-comment + lifecycle lineage propagation

Before Chapter 05 was written, the current canonical source was intentionally evolved so the metadata/governance code follows the same six-layer source-comment contract as Chapters 01–04.

- Chapter 05 core DataHub Python modules now carry local Chinese-first docstrings for exact identity, governance projection, live bootstrap/re-query, bounded Agent reads, and Runtime cutover.
- DataHub governance / read-policy YAML files now explain API semantics and fail-closed boundaries near the contract itself.
- `tests/test_source_comment_contract.py` now covers the Chapter 05 DataHub source surface.
- At the time of this 2026-08-21 Chapter 05 checkpoint, `order_lifecycle_snapshot` was still `REFERENCE_ONLY` / not `semantic-enabled`. That historical state is superseded by the 2026-08-30 lifecycle conversion contract; Phase 7 DataHub Runtime verification still requires lifecycle upstream lineage before any `RUNTIME_VERIFIED` promotion.
- whole repository static suite remains **368 passed**;
- Phase 7 full source/engineering closure remains **PASS**;
- real DataHub GMS / ingestion / exact identity / governance mutation / final re-query evidence remains **DEFERRED**.


## 2026-08-21 Chapter 06 governed-analysis source-comment + canonical contract evolution

Before Chapter 06 was written, the current canonical source was evolved so the Phase 5 / Phase 6 Agent analysis chain follows the same Chinese-first local-comment standard as earlier chapters.

- Router, Semantic Query Planner / Executor, Clarification Continuation, Analysis Session, Time Comparison, Comparative Breakdown, Anomaly Detection, Driver Attribution, Diagnostic Orchestrator, Operational Health, Incident Drilldown, Incident Response, Approval Workflow, Claim Ledger composer and final validator now carry local Chinese-first explanatory docstrings.
- `agent/contracts/phase5_capability_manifest.yml`, `phase6_capability_manifest.yml`, `claim_authority.yml`, and `approval_workflow_policy.yml` now explain their governance/API/runtime boundaries near the contract itself.
- The Phase 5 source-materialization mechanism under `infra/contracts/phase5/canonical_sources/` was evolved together with the current source so `run_phase7_source_closure.sh --repair` no longer overwrites the new comment standard with an older historical copy. Historical milestone ZIP/SHA evidence remains separate.
- `tests/test_source_comment_contract.py` now includes a Chapter 06 Chinese-first contract.
- whole repository static suite is **368 passed**;
- Phase 7 full source/engineering closure is **PASS**;
- real MetricFlow / Dagster / authenticated approval identity / production action execution evidence remains **DEFERRED**.

Chapter 06 authority boundaries remain unchanged by the comment evolution:

- arbitrary SQL / raw predicates are blocked;
- incomplete intent produces clarification instead of guessed query semantics;
- derived comparison / anomaly / attribution claims cannot outrank their underlying Runtime evidence;
- Operational Health reads Dagster exact-partition truth but does not own Recovery execution;
- Driver Attribution is an analytical lens, not causal proof;
- Incident Response is advisory;
- `APPROVED != EXECUTED`; Agent production write authority remains NONE;
- Claim Ledger owns the evidence boundary and LLM remains a constrained renderer.


## 2026-08-21 Chapter 07 Knowledge RAG source/comment evolution

- `agent/knowledge/*.py` now follows the same Chinese-first local docstring standard as Chapters 01–06, including Corpus, Structure-aware Chunking, OpenAI Embedding, Qdrant, Cohere Reranker, Retrieval, Evaluation, Tool and Claim Authority boundaries.
- `knowledge_policy.yml`, `knowledge_retrieval_policy.yml`, `corpus_manifest.yml`, and retrieval Golden Cases now explain their API / evidence / runtime boundaries close to the contract.
- current governed corpus contains **18 active Manifest documents** across business / architecture / modeling / governance / runbook / glossary scopes.
- retrieval source path is defined as Manifest Corpus → stable chunks/SHA → Embedding → Qdrant Dense Retrieval → optional Cohere Rerank → exact Fetch.
- `RETRIEVED_KNOWLEDGE` remains explicitly non-runtime evidence; RAG cannot replace MetricFlow numeric truth, DataHub owner/identity, or Dagster runtime/failure truth.
- `tests/test_source_comment_contract.py` now includes the Chapter 07 Chinese-first contract.
- whole repository static suite is **368 passed**; Phase 7 full source/engineering closure is **PASS**.
- real OpenAI Embedding / Qdrant / Cohere Reranker / retrieval Runtime Evidence remains **DEFERRED**; `.runtime/` evidence is not present in the source package.


## 2026-08-21 Chapter 08 MCP / Runtime Closure source-comment evolution

- `mcp_server/*.py` and `mcp_server/auth/*.py` now follow the same Chinese-first module/class/function/method explanation standard used by Chapters 01–07.
- Governed MCP remains a **protocol adapter**, not a new authority: Deployment Profile controls registration, OAuth Scope controls invocation, and Governed Tool Registry controls execution.
- the MCP surface remains read-only; arbitrary SQL/Shell/Python/File/GraphQL, DataHub write, Dagster launch/backfill/retry/recovery, and Knowledge reindex remain forbidden.
- Streamable HTTP runtime requires OAuth Resource Server + JWT/JWKS verification, Origin validation and DNS rebinding protection; Bearer token passthrough is explicitly forbidden.
- Phase 7C runtime acceptance is source-defined and gated, but real OAuth-protected MCP Runtime Evidence remains **DEFERRED**.
- Phase 7D final evidence aggregator requires **13 / 13** exact Runtime Evidence files to exist, carry `runtime_verified=true`, and match expected status before `PHASE7_END_TO_END_RUNTIME_VERIFIED` can be produced. There is no partial threshold.
- `tests/test_source_comment_contract.py` now includes the Chapter 08 Chinese-first contract and MCP / final-closure structured contract markers.
- whole repository static suite is **368 passed**; Phase 7 full source/engineering closure is **PASS**.
- source package still contains no `.runtime/` evidence; Docker / DataHub / MetricFlow / Dagster / OpenAI / Qdrant / Reranker / MCP OAuth / final end-to-end Runtime remain **DEFERRED**.
- latest blog snapshot is synchronized through V28 terminology / Agent→RAG / multi-format narrative audit.

## Post-Chapter 08 whole-site audit — 2026-08-21

- Chapters 01—08 are complete in the self-contained hash-routed blog.
- At the Chapter 08 whole-site audit, the repository static suite was **368 passed**; Phase 7 full source/engineering closure was **PASS**.
- The final site audit verified **70 / 70** complete source-appendix files against the current canonical tree with no missing file and no text mismatch.
- Historical V27 remains preserved as the Chapter 01—08 pre-Agent-RAG-evolution audit snapshot. The current `blog/commerce-modern-data-platform-latest.html` is V28 and reflects Canonical Source V20/V21 semantics: explicit `KNOWLEDGE_QUERY`, Search→exact Fetch, multi-format document ingestion skeleton, **377 passed**, and unchanged Runtime DEFERRED boundaries.
- Chapter footers no longer expose stale intermediate blog version numbers.
- The homepage Chapter 08 directory entry now includes the same subtopic level as Chapters 01—07.
- The source package still contains no `.runtime/` evidence; all real Docker / Spark / Flink / Kafka / MySQL / Iceberg / Dagster / DataHub / MetricFlow / OpenAI / Qdrant / Reranker / MCP OAuth / end-to-end Runtime claims remain **DEFERRED** unless separately evidenced.

## 2026-08-21 Agent → RAG explicit route + multi-format Knowledge ingestion

Current canonical source now closes two gaps discovered during the final blog review.

### Explicit Agent → Knowledge RAG route

- `Intent.KNOWLEDGE_QUERY` is now a first-class deterministic Agent intent.
- explicit Why / Design / SOP / Runbook markers generate a bounded `search_knowledge -> exact fetch_knowledge` route;
- exact Fetch can only consume `chunk_id` values returned by the prior governed Search result;
- the LLM still cannot provide arbitrary file paths, Qdrant filters, collections or rerank models;
- structured authority keeps precedence: Dataset Runtime questions still go to Dagster, Metric Definition questions still go to MetricFlow;
- Knowledge results enter Claim Ledger as `KNOWLEDGE_EVIDENCE` with `RETRIEVED_KNOWLEDGE`, `runtime_observed=false`;
- when Phase 7B index Runtime evidence is unavailable, the Agent route returns `DEFERRED` instead of treating the knowledge as `NOT_FOUND` or crashing.

### Multi-format Knowledge Document Ingestion

The current active governed corpus still contains **18 Markdown documents**. That current fact has not been rewritten.

New source-defined ingestion adapters now support a unified pre-chunking contract for:

- Markdown: UTF-8 + YAML Front Matter governance;
- text-layer PDF: `pypdf` extraction with page provenance;
- DOCX: `python-docx` heading / paragraph / table structure;
- scanned/image-only PDF: **Fail Closed** when no text layer exists; OCR/layout extraction remains **DEFERRED**.

Latest source/static acceptance after this evolution: **377 passed**; Phase 7 full source/engineering closure: **PASS**.

PDF / DOCX governance metadata must be complete in the Manifest because those formats do not carry the project's Markdown YAML Front Matter contract. All formats normalize to `KnowledgeDocument / KnowledgeBlock` before Structure-aware Chunking.

Evidence boundary:

- parser / router / static contract tests: SOURCE / STATIC only;
- real enterprise PDF/DOCX corpus ingestion: NOT EXECUTED;
- OCR Runtime: DEFERRED;
- real OpenAI Embedding / Qdrant / Cohere Reranker: DEFERRED;
- `RETRIEVED_KNOWLEDGE` remains non-runtime evidence and cannot replace MetricFlow / DataHub / Dagster authority.


## V28 terminology + Agent/RAG blog synchronization — 2026-08-21

- Chapter 06 now explicitly names itself as the Governed Analysis Core rather than implying the final Agent is complete before Knowledge RAG.
- Chapter 07 now shows the real `KNOWLEDGE_QUERY -> search_knowledge -> exact fetch_knowledge` Agent route from Canonical Source V20.
- Chapter 07 now documents the source-defined Markdown / text-PDF / DOCX canonical document ingestion contract and the scanned-PDF OCR fail-closed boundary.
- the whole site now uses a richer clickable terminology system: each governed term can explain plain meaning, project-specific responsibility, and common misinterpretation.
- current blog status prose is synchronized to **377 passed**; real Runtime claims remain **DEFERRED**.
- V27 remains preserved as historical blog evidence; V28 is the current latest blog snapshot.


## 2026-08-21 Serving Layer + Trino evolution

The current source adds a fixed-consumption path without changing MetricFlow metric authority:

- MetricFlow remains the only metric-definition authority for BI, API and Agent consumption.
- `serving/contracts/bi_daily_executive.yml` declares a fixed metric/dimension selection and output Grain; raw SQL/formulas are rejected by the contract loader.
- Dagster owns `serving_daily_export_job` and the daily Serving Asset; the export fails closed when required exact-partition upstream Asset materializations are missing.
- Spark materializes the MetricFlow result to `polaris.serving.bi_daily_executive` with an exact-partition Iceberg overwrite filter, including safe replacement by an empty result.
- Trino 483 is wired to the existing Polaris REST Catalog and RustFS object storage as the read/query serving engine.
- BI reads the Serving table through Trino; FastAPI exposes only fixed read endpoints and no arbitrary SQL/metric surface.
- Agent dynamic analytics continues to query MetricFlow directly and does not use Serving tables as a second semantic source.
- Architecture sources are stored under `docs/architecture/` in Mermaid and Graphviz form, with a rendered SVG.

Static/source acceptance after this evolution: **385 passed**. `infra/runtime/run_serving_static.sh` passes.
Real Docker / Polaris / Spark / MetricFlow / Dagster / Trino / FastAPI end-to-end Serving runtime execution is **NOT EXECUTED in this environment** because those runtime binaries/services are unavailable here.


## 2026-08-21 Serving governance / consumer lineage extension

The Serving Layer is now included in the DataHub governance contract without changing Metric Authority:

- `commerce_polaris.serving.bi_daily_executive` is governed as a rebuildable Iceberg Serving Dataset;
- `commerce.governance.metricAuthority = METRICFLOW`;
- `commerce.governance.agentReadiness = REFERENCE_ONLY`, so Agent analytics still query MetricFlow;
- Dagster Serving Export is modeled as DataFlow/DataJob lineage from `orders`, `order_items`, `refund_items` to the Serving Dataset;
- BI consumption is represented as Dataset -> Dashboard lineage;
- FastAPI metadata is exported as OpenAPI and ingested as endpoint metadata; API endpoint Dataset URNs must be exact-runtime-resolved before lineage writes;
- fuzzy or guessed consumer identity binding is forbidden; all real DataHub mutations default fail closed.

Static acceptance after this extension: **391 passed**. Real DataHub consumer-lineage Runtime evidence remains unexecuted in the current environment.

## 2026-08-21 Final project completeness / Chapter 09 runtime-closure audit

- Serving / Trino / FastAPI / Consumer Governance source comments are now enforced by the same Chinese-first comment contract used by the rest of the blog/source surface.
- Final Runtime Closure now requires **13 / 13** exact evidence components: the previous 11 Agent/RAG/MCP runtime components plus `SERVING_RUNTIME_VERIFIED` and `SERVING_GOVERNANCE_RUNTIME_VERIFIED`.
- `infra/runtime/serving_runtime_acceptance.py` performs Trino ↔ FastAPI partition reconciliation and Iceberg snapshot verification before writing runtime evidence.
- Serving DataHub `verify-all` now final-requeries DataFlow/DataJob, logical Dashboard and API endpoint upstream lineage before writing governance runtime evidence.
- DataHub OpenAPI recipe runtime coordinates were corrected to the actual host-facing Serving API (`localhost:8081`) and committed OpenAPI schema is contract-tested against the FastAPI app.
- Root `pytest.ini` now supports direct `pytest -q`; root README is aligned to the current Agent + BI + API architecture.
- Current whole-repository source/static suite: **403 passed**.
- Real 13/13 Runtime Evidence remains **DEFERRED** until executed on a workstation with the required services, credentials and exact consumer identities.


## GitHub CI + dependency-lock hardening — 2026-08-21

- `.github/workflows/ci.yml` now enforces a lightweight static gate, the full repository contract suite, and a 10-environment dependency-resolution matrix.
- `.github/workflows/dependency-locks.yml` generates the complete per-runtime Python 3.11 / Linux x86_64 hash-lock set as a reviewable artifact.
- `requirements/locks/LOCK_POLICY.yml` freezes Python, platform, uv version, resolution strategy and package-publication cutoff.
- `scripts/lock_dependencies.sh` intentionally keeps canonical dbt and MetricFlow compatibility in separate environments.
- `scripts/check_dependency_locks.py` rejects partial checked-in lock sets and locks without SHA-256 package hashes.
- Current source/static suite after this hardening: **403 passed**.
- Full transitive lock contents are **not fabricated in this offline execution environment**; the first complete lock set must be resolved once on an online workstation/GitHub Actions and then committed.

## 2026-08-30 Metric Version Lifecycle current canonical evolution

- `metadata/datahub/governance/metric_registry.yml` now declares the current governed `current_version` for every admitted Metric.
- `metadata/datahub/governance/metric_lifecycle.yml` is the append-only business-version ledger with status, change type, effective-time metadata, supersedes relation, owners and canonical definition fingerprint.
- `metadata/datahub/tools/validate_metric_lifecycle.py` fails closed on missing lifecycle rows, invalid current-version state, missing V2+ effective dates / supersedes relations, and silent ACTIVE-definition fingerprint drift.
- `agent/context/repository.py` exposes current Metric business-version/lifecycle metadata without moving formula authority out of dbt + MetricFlow.
- `.github/workflows/ci.yml` now runs the lifecycle validator and lifecycle contract tests in the static-quality gate.
- `docs/METRIC_VERSION_LIFECYCLE.md` documents Breaking Change classification, V1→V2 rollout, Backfill vs Forward Fix, Golden Oracle update rules and Runtime evidence boundaries.
- Static lifecycle governance is implemented; real historical-version dual-run / Backfill / Forward Fix Runtime evidence is not claimed.

- Whole-repository source/static suite after Metric Version Lifecycle governance: **413 passed**.
