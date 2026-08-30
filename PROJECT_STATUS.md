# Project Status — 2026-08-18 Phase 3C Consolidated Snapshot

## Phase 1 — Business & Modeling Design
- [x] Shopify Order Domain
- [x] Entity / State / Event / Grain
- [x] Business Version（业务版本）
- [x] Business Time / Source Updated Time / Observation Time
- [x] Fanout / Join Safety

## Phase 2 — Lakehouse / dbt / Semantic / Acceptance
- [x] Raw Iceberg
- [x] Structured Source Business Version MERGE
- [x] dbt Source / Staging
- [x] Incremental Current State
- [x] Business Marts: Sales / Payment / Refund / Fulfillment
- [x] Order Lifecycle Accumulating Snapshot source model (1 Order = 1 Row)
- [x] Lifecycle affected-order propagation across Order / Transaction / Refund / Fulfillment / FulfillmentEvent
- [ ] Real dbt + Spark + Iceberg runtime evidence for Order Lifecycle Accumulating Snapshot
- [x] Semantic Layer / MetricFlow model
- [x] Golden Dataset / Oracle / Comparator design
- [x] Business Version A → B → A acceptance scenario
- [ ] Clean-room real runtime acceptance in this environment

## Phase 3A — Dagster Asset Model
- [x] Asset Graph design
- [x] Raw / Structured Source custom Assets
- [x] dbt Assets
- [x] Asset Checks ownership boundary
- [x] Control Plane ≠ Execution Plane

## Phase 3B — Time / Incremental Model
- [x] Daily Partition / Execution Window
- [x] 5-minute source-read lookback contract
- [x] Changed Keys → Affected Keys → Complete Current Context
- [x] Incremental Iceberg MERGE
- [x] Business-time physical layout design
- [x] Dagster Partition ≠ Iceberg Physical Partition
- [ ] Real EXPLAIN / Iceberg pruning evidence
- [ ] Real multi-partition Backfill runtime evidence

## Phase 3C — Automation Model
- [x] Daily Schedule: 00:15 UTC
- [x] Bounded Step Retry: max 2
- [x] Consumer Freshness deadline: 01:00 UTC
- [x] Recovery Candidate horizon
- [x] Exact-partition state reader
- [x] Structured Failure Classification
- [x] Recovery Decision policy
- [x] Bounded Recovery Sensor
- [x] Stable recovery Run Key / exact Partition replay contract
- [x] dbt run_results.json classified failure adapter
- [x] Hand-authored Phase 3C acceptance oracle
- [x] Runtime acceptance plan
- [ ] Real Dagster Definitions validation
- [x] R01 Runtime evidence harness (ScheduleDefinition + same-run 9/9 verifier)
- [x] R02 missed-schedule policy + SensorDefinition acceptance harness
- [x] R02 safety boundary: historical no-run gaps require manual backfill
- [x] R03 infrastructure outage/recovery policy + local Dagster acceptance harness
- [x] R03 Step Retry adapter probe contract: max 2 retries / infrastructure failure tags
- [x] R04 bounded-wait contract: repeated infra-down Sensor ticks do not consume replay budget
- [x] R04 local acceptance harness: 3 down ticks -> no RunRequest -> restore still attempt-1
- [x] R05 acceptance-only dbt Data Test injection (default PASS, explicit var forces FAIL)
- [x] R05 structured dbt failure contract: run_results test fail -> data_contract -> no Step Retry
- [x] R05 recovery fail-closed harness: data_contract -> ALERT_MANUAL -> no RunRequest
- [x] R06 acceptance-only dbt parse/compiler-error injection (default project remains valid)
- [x] R06 classification boundary: parse failure -> deterministic_code; generic compile failure -> unknown
- [x] R06 recovery fail-closed harness: deterministic_code -> no Step Retry -> ALERT_MANUAL -> no RunRequest
- [x] R07 duplicate-recovery guard: persisted active recovery owns exact partition and blocks another RunRequest
- [x] R07 precedence contract: active owner WAIT occurs before replay-budget exhaustion
- [x] R08 replay-budget guard: failed attempt-1 -> ALERT_MANUAL / no attempt-2
- [x] R08 persisted-budget contract: recovery budget comes from Run Storage, not Sensor poll count
- [x] R09 success-vs-completeness guard: Run SUCCESS with 8/9 exact-partition Marts -> ALERT_MANUAL
- [x] R09 local event-store harness: same-run materializations expose exact missing Mart and forbid auto replay
- [x] R10 current-completeness guard: historical FAILURE + current 9/9 exact-partition Marts -> NO_ACTION
- [x] R10 local event-store harness: independent repair/backfill materializations override stale failure history
- [x] R11 freshness guard: before 01:00 deadline -> WAIT / no recovery permission
- [x] R11 candidate boundary: 2026-08-05 excluded at 00:40 and eligible from exactly 01:00 UTC
- [x] R12 unknown fail-closed guard: ambiguous structured evidence -> UNKNOWN -> no Step Retry
- [x] R12 recovery guard: UNKNOWN -> ALERT_MANUAL / no cross-run Auto Replay
- [x] R13 transient-runtime guard: running service + timeout -> bounded Step Retry -> one eligible replay
- [x] Phase 3C Closure Audit: missing check jobs / time-contract drift / dead failure class / replay-history cap fixed
- [x] Phase 3C static closure runner and regression contract
- [ ] R01-A real loaded ScheduleDefinition evaluation
- [ ] R01-B real daemon schedule tick
- [ ] R01-C same-run 9/9 exact-partition completion before deadline
- [ ] R02-A real SensorDefinition evaluation with persistent Dagster test instance
- [ ] R02-B real daemon run-key dedup evidence
- [ ] R02-C recovery-run same-run 9/9 exact-partition completion
- [ ] R03-A local Dagster adapter retry + Sensor transition execution
- [ ] R03-B real Docker outage / real Step Retry evidence
- [ ] R03-C real daemon recovery after infrastructure restore
- [ ] R03-D recovery-run same-run 9/9 exact-partition completion
- [ ] R04-A real local Dagster repeated-wait Sensor execution
- [ ] R04-B real daemon + Docker repeated-down / restore evidence
- [ ] R05-A real local Dagster + dbt acceptance execution
- [ ] R05-B real daily-partition data-contract failure evidence
- [ ] R06-A real local Dagster + dbt parse acceptance execution
- [ ] R06-B real daily-partition deterministic project/code failure evidence
- [ ] R07-A real local Dagster active-owner Sensor execution
- [ ] R07-B real daemon stable-run-key duplicate suppression evidence
- [ ] R08-A real local Dagster failed-attempt budget execution
- [ ] R08-B real daemon/Docker attempt-1 failure -> no attempt-2 evidence
- [ ] R09-A real local Dagster success/incomplete event-store execution
- [ ] R09-B real Daily Pipeline SUCCESS/incomplete consumer evidence
- [ ] R10-A real local Dagster failed-history + complete-partition event-store execution
- [ ] R10-B real dbt/Spark repair/backfill -> 9/9 exact-partition data evidence
- [ ] R11-A real local Dagster freshness-guard Sensor execution
- [ ] R11-B real daemon/Freshness timing evidence before and at deadline
- [ ] R12-A real local Dagster unknown-failure adapter + Sensor execution
- [ ] R12-B real Docker/Spark ambiguous fault + daemon/manual-escalation evidence
- [ ] R13-A real local Dagster transient-timeout retry + Sensor execution
- [ ] R13-B real Spark timeout / daemon recovery / 8-of-8 completion evidence
- [ ] Real Schedule / Freshness / Sensor daemon evidence
- [ ] Real failure → restore → replay acceptance

## Phase 4 — Metadata & Agent
- [x] Metadata / governance engineering contracts (runtime still deferred)
- [x] Governed Agent read/query tool boundary
- [x] Evidence-first answer boundary
- [x] Governed semantic query planning
- [x] Structured dimension filters + multi-metric query
- [x] Dimension value discovery / resolution engineering
- [x] Clarification continuation
- [x] Phase 5F governed analysis-session state
- [x] Phase 5G governed time context + comparative analysis
- [x] Phase 5H governed comparative breakdown + contribution analysis
- [ ] Real DataHub Runtime acceptance
- [ ] Real MetricFlow/Spark/Polaris query acceptance
- [ ] Production session store / authentication

## Phase 5 Closure

- [x] Phase 5A governed single-metric semantic query
- [x] Phase 5B structured filters + multi-metric query
- [x] Phase 5C dimension-value discovery
- [x] Phase 5D dimension-value resolution
- [x] Phase 5E clarification continuation
- [x] Phase 5F governed analysis-session state
- [x] Phase 5G governed time comparison
- [x] Phase 5H comparative breakdown + contribution analysis
- [x] Phase 5 source/schema/router/generated-evidence closure audit
- [ ] Real MetricFlow/Spark/Polaris query acceptance
- [ ] Production session store / authentication / authorization

Current static closure evidence:

```text
296 / 296 repository tests PASS at the Phase 6 Final Static Closure snapshot
Python compile PASS
Phase 5 shell syntax PASS
Generated source-reference integrity PASS
Public Tool schema ↔ executable surface PASS
All live wrappers default REFUSED / exit 2 PASS
```

Run the canonical closure entry point:

```bash
./infra/runtime/run_phase5_static_closure.sh
```

Real DataHub / Dagster / MetricFlow / Spark / Polaris / OpenAI runtime evidence remains
**DEFERRED**. See `docs/PHASE5_CLOSURE_AUDIT.md`.


## Phase 6 — Governed Diagnostics

- [x] Phase 6A governed anomaly detection / median baseline
- [x] Operational-health gate contract
- [x] Phase 6B governed Region / Brand / Category driver attribution
- [x] Additive contribution reconciliation / non-additive guard
- [x] Phase 6C diagnostic orchestrator: anomaly → exact-partition health → drivers
- [x] Phase 6C evidence projection into Phase 4F Claim Ledger
- [x] Phase 6D operational incident drilldown: missing marts / failed run / failure stage / recovery status
- [x] Phase 6D reuses Phase 3C failure classification + recovery policy; no free-text cause inference
- [x] Phase 6E advisory incident-response planner + explicit action authority / human approval boundary
- [x] Phase 6E delegates AUTO_REPLAY to the existing Dagster Recovery Sensor; Agent has no recovery/backfill write authority
- [x] Phase 6F governed approval workflow: `PENDING → APPROVED / REJECTED / EXPIRED`
- [x] Phase 6F binds approval to exact partition/action/evidence fingerprints and invalidates stale evidence
- [x] Phase 6F Agent self-approval/execution prohibited; approval audit events are hash-chained
- [x] Runtime-observed claim requires `RUNTIME_VERIFIED` validator guard
- [x] Phase 6A / 6B / 6C / 6D / 6E / 6F live wrappers fail closed by default
- [ ] Real Dagster exact-partition operational-health acceptance
- [ ] Real MetricFlow anomaly / driver runtime acceptance
- [ ] Real constrained LLM diagnostic response acceptance

Current Phase 6 static evidence:

```text
Phase 6A–6F + final closure contracts: PASS
Whole repository: 296 / 296 PASS
Phase 5 static closure: PASS
Phase 6 final static closure: PASS
Frozen contract lock: PASS
Real runtime evidence: DEFERRED
```

Canonical Phase 6 closure entry point:

```bash
./infra/runtime/run_phase6_static_closure.sh
```

## Phase 6 Final Closure

- [x] Runtime-gate Policy ↔ Manifest ↔ Live Wrapper alignment
- [x] Final authority matrix: MetricFlow semantic authority / Phase 3C recovery execution / Phase 6 read-only diagnosis + advisory response + approval state
- [x] `APPROVED != EXECUTED` frozen boundary
- [x] Phase 6 SHA-256 contract lock
- [x] `SOURCE_STATE.md` updated from old Phase 3C snapshot to current Phase 6 closure
- [x] Deterministic/static Phase 6 samples rebuilt by canonical closure runner
- [x] Whole repository regression and default-refusal runtime gates
- [ ] Phase 7 real workstation runtime acceptance

## Current next step

**Phase 7 · Real Runtime.** Static feature expansion is paused. The next work is to start the real
Dagster / Spark / Polaris / dbt / MetricFlow / DataHub stack, convert selected `DEFERRED` evidence
to concrete `RUNTIME_VERIFIED` observations, and run the end-to-end diagnostic / incident / approval
acceptance path. Phase 6 is frozen at 6A–6F.

A new milestone source package is produced at Phase 6 Final Static Closure.

## R13 consolidated addition

R13 adds the missing transient-runtime acceptance harness. A command timeout while
`spark-thrift` still reports Running is classified as `transient_runtime`, receives the
bounded production Step Retry budget (`max_retries=2`), and after final Run failure may
receive exactly one cross-run replay only when Freshness is overdue, current runtime health
is good, no active owner exists, the exact partition is incomplete, and replay budget is 0.

```text
service Running + command Timeout
    -> transient_runtime
    -> 3 total step attempts
    -> failed exact-partition Run
    -> runtime healthy
    -> AUTO_REPLAY attempt-1
```

R13-A is a local persistent-Dagster harness and does not prove a real Spark timeout, real
daemon-created recovery Run, or 9/9 data completion. Those remain deferred Runtime evidence.


## 2026-08-20 lifecycle SLA promotion

- [x] `order_lifecycle_snapshot` is now included in the current nine-Mart Freshness / Recovery SLA source contract.
- [x] Exact-partition current reader / Freshness / Sensor acceptance use the post-baseline nine-Mart registry.
- [x] Phase 6 frozen automation and agent source remains byte-for-byte preserved.
- [x] DataHub expected identity/governance projection includes the lifecycle dataset as `REFERENCE_ONLY`.
- [ ] Lifecycle MetricFlow semantic model/metrics: intentionally NOT added until an explicit semantic contract is designed.
- [ ] Real Dagster/Spark/Iceberg nine-Mart Runtime completion evidence: DEFERRED.

## 2026-08-20 current canonical source comment re-closure

- [x] Historical Phase 6/7 milestone ZIPs remain immutable evidence; the current canonical source is allowed to evolve.
- [x] Chapter 01–04 core Python functions/methods have local Chinese-first docstrings.
- [x] SQL/dbt/GraphQL/YAML/Flink source comments follow the six-layer project standard.
- [x] `docs/SOURCE_COMMENT_STANDARD.md` defines the current rule.
- [x] `tests/test_source_comment_contract.py` protects the comment contract.
- [x] Current Phase 6 tracked-file lock explicitly re-generated after intentional current-tree edits.
- [x] Whole repository static suite: **366 passed**.
- [ ] Real Docker / Spark / Flink / Kafka / MySQL / Iceberg / Dagster / DataHub / MetricFlow / Qdrant / MCP / OpenAI Runtime Evidence remains **DEFERRED**.


## 2026-08-21 Current canonical evolution

- [x] Chapter 06 governed-analysis comment contract added for Router → Semantic Query → Clarification/Session → Comparison/Breakdown → Anomaly/Attribution → Diagnostic/Incident → Approval/Claim Ledger.
- [x] Phase 5 canonical source-materialization copies evolved together with current source; source closure no longer reverts local Chinese-first comments.
- [x] Whole repository static suite: **366 passed**.
- [x] Phase 7 full source/engineering closure: **PASS**.
- [ ] Real MetricFlow / Dagster / authenticated approval / external execution Runtime evidence: **DEFERRED**.


## 2026-08-21 Chapter 07 Knowledge RAG current canonical evolution

- [x] Governed Corpus / Chunking / Embedding / Qdrant / Reranker / Retrieval / Evaluation source is defined.
- [x] Knowledge source-comment contract upgraded to Chinese-first module/class/function/method explanations.
- [x] 18 active Manifest documents and retrieval Golden Cases remain source/static validated.
- [x] Structured authority precedence remains enforced: RAG cannot override MetricFlow / DataHub / Dagster truth.
- [x] Whole repository static suite: **368 passed**.
- [x] Phase 7 full source/engineering closure: **PASS**.
- [ ] Real Qdrant / OpenAI Embedding / Cohere Reranker / governed retrieval Runtime evidence: **DEFERRED**.


## 2026-08-21 Chapter 08 MCP + Final Runtime Closure current canonical evolution

- [x] Commerce MCP read-only protocol surface: Tool / Resource / Prompt.
- [x] Deployment Profile + OAuth capability Scope + Governed Registry three-layer authorization contract.
- [x] Streamable HTTP OAuth Resource Server / JWT / JWKS / Origin / DNS rebinding security source.
- [x] Bearer token passthrough explicitly forbidden.
- [x] Phase 7C MCP live acceptance runner / evidence contract source-defined.
- [x] Phase 7D final evidence aggregator requires 13/13 verified runtime components; no partial threshold.
- [x] Chapter 08 source comments normalized to Chinese-first local explanations.
- [x] Whole repository static suite: **368 passed**.
- [ ] Real OAuth-protected MCP runtime: **DEFERRED**.
- [ ] `PHASE7_END_TO_END_RUNTIME_VERIFIED`: **DEFERRED** until all 13 runtime evidence components are verified.

## Whole-site final audit — 2026-08-21

- [x] Chapter 01—08 engineering narrative complete.
- [x] Current site status references normalized to **368 passed**.
- [x] Complete source appendix parity against canonical source: **70 / 70**.
- [x] Stable chapter footer / homepage Chapter 08 directory parity completed.
- [x] Current canonical source remains source/static closed.
- [ ] Real end-to-end Runtime verification remains **DEFERRED** until the required evidence files are produced.

## 2026-08-21 Agent-RAG integration + multi-format document ingestion evolution

- [x] `KNOWLEDGE_QUERY` added to the deterministic Agent routing contract.
- [x] Why / Design / SOP / Runbook questions now source-define `search_knowledge -> exact fetch` execution.
- [x] Dataset Runtime and Metric Definition keep precedence over RAG.
- [x] Claim Ledger accepts retrieved knowledge only as `RETRIEVED_KNOWLEDGE`, never Runtime truth.
- [x] Markdown / text-PDF / DOCX normalize into a common `KnowledgeDocument / KnowledgeBlock` contract before Chunking.
- [x] PDF page provenance and DOCX heading/paragraph/table structure are preserved in source-defined chunks.
- [x] image/scanned PDF without a text layer fails closed; OCR remains DEFERRED.
- [x] current active Manifest corpus remains 18 Markdown documents; no fake PDF/DOCX enterprise corpus was added.
- [ ] Real enterprise PDF / DOCX ingestion Runtime: NOT EXECUTED.
- [ ] OCR / layout-analysis Runtime: DEFERRED.
- [ ] Real Qdrant / OpenAI Embedding / Cohere Reranker Agent retrieval Runtime: DEFERRED.

- [x] Whole repository static suite after Agent-RAG / multi-format source evolution: **377 passed**.
- [x] Phase 7 full source/engineering closure after the evolution: **PASS**.
- [x] Blog V28 synchronizes Chapter 06/07 with the accepted Agent→RAG / multi-format source evolution and current **377 passed** static state; historical V27 remains preserved.


### V28 terminology system / Agent-RAG narrative sync

- [x] Chapter 06 distinguishes Governed Analysis Core from the final knowledge-augmented Agent.
- [x] Chapter 07 shows explicit KNOWLEDGE_QUERY → governed Search → exact Fetch.
- [x] Chapter 07 describes Markdown / text-PDF / DOCX normalization and OCR/layout DEFERRED boundary.
- [x] Major project terminology uses plain-language clickable explanations without replacing accurate English technical names.
- [x] Current blog static status synchronized to **377 passed**; Runtime Evidence remains DEFERRED.


## Serving Layer + Trino — 2026-08-21

- [x] Fixed BI/API Serving Contract references governed MetricFlow metrics; formulas/raw SQL are forbidden.
- [x] Dagster daily Serving Export Asset / Job / Schedule source is registered.
- [x] Exact-partition upstream readiness gate prevents publishing a partial business day.
- [x] Spark/Iceberg materializer performs explicit casts, Grain uniqueness checks and atomic exact-partition replacement.
- [x] Trino 483 Compose service reads Iceberg through Polaris REST Catalog and RustFS.
- [x] BI read path documented as `BI -> Trino -> Iceberg Serving`.
- [x] FastAPI fixed endpoints read through Trino; arbitrary SQL and caller-defined metrics are not exposed.
- [x] Editable Mermaid/Graphviz architecture source and rendered SVG are stored under `docs/architecture/`.
- [x] Serving static runner passes; whole repository static suite: **391 passed**.
- [ ] Real MetricFlow export -> Spark/Iceberg Serving -> Trino -> FastAPI/BI runtime acceptance: NOT EXECUTED in the current environment.
- [x] Serving governance / downstream lineage: DataHub contract covers Serving Dataset, Dagster DataFlow/DataJob, BI Dashboard and OpenAPI endpoint metadata.
- [x] Serving Dataset records `Metric Authority = METRICFLOW` and `Agent Readiness = REFERENCE_ONLY`.
- [x] API endpoint lineage refuses guessed/fuzzy URNs and requires exact runtime identity evidence after OpenAPI ingestion.
- [ ] Real DataHub Serving governance / BI/API lineage write + final re-query: NOT EXECUTED in the current environment.

Current architecture-completion rule: do not add another metric definition layer or OLAP database by default. Trino is the query-serving engine for the open Iceberg path; Doris/StarRocks remains an optional future optimization only if a real high-concurrency/low-latency SLA justifies it.

## Final completeness audit — 2026-08-21

- [x] Chapter 09 Serving / Trino / FastAPI / Consumer Governance Chinese-first comment contract.
- [x] Serving runtime evidence contract: Trino query + Iceberg snapshot + FastAPI readiness + Trino/API partition reconciliation.
- [x] Serving governance `verify-all` final re-query path.
- [x] Final Runtime Closure extended from 11/11 to **13/13** required evidence components.
- [x] OpenAPI DataHub recipe corrected to `localhost:8081` + repository OpenAPI drift test.
- [x] Root pytest import configuration + current-architecture README.
- [x] Whole repository source/static suite: **403 passed**.
- [ ] Real 13/13 end-to-end Runtime Closure: **DEFERRED** until workstation execution.
- [x] GitHub CI matrix: static-quality + full contract suite + 10-environment dependency-resolution matrix.
- [x] Per-runtime dependency-lock policy / generator / validator / GitHub lock workflow.
- [ ] First online generation + commit of the full transitive hash-lock files (current execution environment cannot reach PyPI).


## GitHub CI / dependency lock hardening — 2026-08-21

- [x] Python 3.11 declared as the CI/lock baseline through `.python-version`.
- [x] Separate CI requirement surface avoids merging dbt 1.12 and MetricFlow compatibility dbt 1.11.
- [x] GitHub Actions uses read-only repository permissions and isolated dependency jobs.
- [x] Dependency resolution uses uv with SHA-256 hashes and a frozen publication cutoff.
- [x] Partial committed lock sets fail validation; zero committed locks remains an explicit bootstrap state.
- [x] Whole repository source/static suite after CI contracts: **403 passed**.
- [ ] Run the online Dependency Locks workflow once and commit all 10 generated lock files.

## Metric Version Lifecycle Governance — 2026-08-30

- [x] Current governed Metric registry declares `current_version`.
- [x] Append-only Metric lifecycle ledger covers all 17 governed metrics.
- [x] Lifecycle states: `DRAFT / ACTIVE / DEPRECATED / RETIRED`.
- [x] Change classes: `BASELINE / NON_BREAKING / BREAKING`.
- [x] V2+ requires explicit `effective_from` and `supersedes_version`.
- [x] SHA-256 canonical definition fingerprint blocks silent ACTIVE metric semantic rewrites.
- [x] CI static-quality gate executes lifecycle validation + regression tests.
- [x] Agent governed Metric Context exposes current business version and lifecycle state.
- [x] V1→V2 / Backfill / Forward Fix / Golden Oracle governance procedure documented.
- [ ] Real MetricFlow dual-version Runtime execution: NOT EXECUTED.
- [ ] Real Backfill or Forward Fix migration evidence: NOT EXECUTED.
- [x] Whole repository source/static suite after Metric Version Lifecycle governance: **413 passed**.
