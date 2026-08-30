# AI-Native Governed Data Platform & Data Agent

> 一个以 Shopify Commerce 为业务场景的现代数据平台 + Governed Data Agent 工程。
>
> 核心目标不是“自然语言生成 SQL”，而是让 **Business Facts、Metric Definitions、Runtime Evidence、Governance Identity、Knowledge Evidence** 在同一套权威边界下，同时服务 Agent、BI 和业务 API。

## Architecture

```text
Commerce Sources
      ↓
Source-aware Ingestion
      ↓
Iceberg Raw / Source / Realtime
      ↓
Business Version + dbt Marts
      ↓
MetricFlow ───────────────────────────────→ Governed Agent
Metric Authority                                  ↑
      │                                           │
      └→ Fixed Serving Contract                   │
             ↓                                    │
          Dagster                                 │
             ↓                                    │
      Iceberg Serving                             │
             ↓                                    │
           Trino                                  │
        ┌────┴────┐                               │
        ↓         ↓                               │
       BI      FastAPI → Business Apps            │
                                                    │
DataHub Governance + Runtime Evidence + RAG ────────┘
```

Editable architecture source:

```text
docs/architecture/AI_NATIVE_DATA_AGENT.mmd
docs/architecture/AI_NATIVE_DATA_AGENT.dot
docs/architecture/AI_NATIVE_DATA_AGENT.svg
```

## What the project implements

### Source truth

Different sources keep different semantics instead of being forced into one ingestion pattern:

- Shopify Admin GraphQL → **Observation**
- MySQL binlog / Flink CDC → **Change / Changelog**
- Behavior Collector → Kafka → PyFlink → **Event**

### Business facts and modeling

- Raw Observation → deterministic Business Version
- dbt Source / Staging / Intermediate / Marts responsibility split
- Transaction Facts, Current State, Event and Lifecycle Snapshot
- Affected-key incremental modeling and rollback-safe version semantics

### Semantic layer

MetricFlow owns metric definitions and business-time semantics. Consumers do not redefine formulas.

Metric governance now includes an append-only **Metric Version Lifecycle** contract: `metric_registry.yml` points to the current business version, `metric_lifecycle.yml` keeps historical status/effective-time/supersedes metadata, and CI uses a SHA-256 definition fingerprint to block silent rewrites of an ACTIVE metric. See `docs/METRIC_VERSION_LIFECYCLE.md`.

Order lifecycle conversion is governed from the one-row-per-Order `order_lifecycle_snapshot`, including Order → Paid within 24h, Order → Fulfillment within 3d, and Order → Delivered within 7d. The conversion contract stays on Order Grain instead of joining Payment / Fulfillment detail facts directly.

```text
MetricFlow
   ├── Dynamic semantic query → Agent
   └── Fixed Serving Contract → BI / API
```

### Orchestration and recovery

Dagster owns logical partitions, Asset dependencies, schedules, Freshness, structured failure classification and bounded recovery decisions.

Key boundary:

```text
Schedule ≠ Freshness
Run SUCCESS ≠ Exact Partition Completeness
Step Retry ≠ Cross-run Recovery
```

### Governance

DataHub manages exact Dataset identity, Domain, Owner, Tags, Glossary, Structured Properties and bounded Lineage.

Serving governance extends to:

```text
Marts → Dagster DataFlow/DataJob → Serving Dataset
                                  ├→ Dashboard
                                  └→ OpenAPI Endpoint Dataset
```

### Governed Agent

The Agent supports:

- Semantic Q&A
- Clarification and multi-turn analysis state
- Time comparison
- Dimension breakdown
- Anomaly detection
- Driver attribution
- Operational diagnosis
- Incident drilldown and advisory response
- Human approval lifecycle
- Governance Q&A
- Knowledge RAG / SOP / troubleshooting

The Agent remains read-oriented and authority bounded. `APPROVED` does not mean `EXECUTED`.

### Knowledge RAG

Governed Corpus → structure-aware chunking → Embedding → Qdrant → optional Rerank → exact Fetch.

RAG owns explanatory evidence such as Why / Design / SOP / Troubleshooting. It cannot replace MetricFlow numeric truth, DataHub identity/ownership, or Dagster runtime facts.

### MCP runtime

MCP is a protocol adapter, not a new authority.

```text
Deployment Profile → OAuth Scope → Governed Tool Registry → Execution
```

Arbitrary SQL / Shell / Python / DataHub mutation / Dagster launch are not exposed to the Agent.

### Serving layer

Fixed BI/API workloads reuse MetricFlow semantics but may use a different physical path:

```text
MetricFlow
  → Serving Contract
  → Dagster
  → Spark exact-partition Iceberg overwrite
  → Trino
  → BI / FastAPI
```

Serving tables are rebuildable projections, not new Business Truth.

## Repository map

```text
ingestion/                       Source-specific ingestion
lakehouse/                       Raw → Business Version / physical jobs
dbt/mercaso_dbt/                dbt models + MetricFlow semantic definitions
orchestration/dagster/           Assets / checks / schedules / recovery
metadata/datahub/                Governance contracts + runtime tools
agent/                           Governed analytics / diagnostics / RAG tools
knowledge/                       Governed knowledge corpus
mcp_server/                      Governed MCP protocol surface
serving/                         BI/API contracts, export and FastAPI
infra/trino/                     Trino Iceberg query-serving configuration
infra/runtime/                   Static + real runtime acceptance runners
acceptance/                      Recovery acceptance scenarios
tests/                           Static / contract / unit acceptance
docs/                            Architecture, design decisions and runbooks
```

## Static validation

From the repository root:

```bash
pytest -q
```

Current source/static suite:

```text
413 passed
```

Focused closures are also available under `infra/runtime/`, for example:

```bash
./infra/runtime/run_phase6_static_closure.sh
./infra/runtime/run_phase7_source_closure.sh
./infra/runtime/run_serving_static.sh
./infra/runtime/run_serving_governance_static.sh
```

Static/source PASS is not Runtime PASS.

## Real runtime acceptance

The project intentionally keeps runtime evidence under `.runtime/` and excludes it from Git.

The final closure requires **13 / 13 exact Runtime Evidence components**. It includes the original Agent/RAG/MCP runtime plus the new Serving and Serving-Governance paths.

The fixed Serving runtime is executed with an explicit gate:

```bash
SERVING_ALLOW_RUNTIME_ACCEPTANCE=true \
./infra/runtime/run_serving_runtime.sh 2026-08-20
```

The final full closure requires a prepared business partition, real provider credentials, OAuth/JWKS configuration, DataHub runtime, and exact OpenAPI Endpoint Dataset URNs:

```bash
PHASE7_ALLOW_FINAL_RUNTIME_CLOSURE=true \
SERVING_ACCEPTANCE_PARTITION_KEY=2026-08-20 \
./infra/runtime/run_phase7_final_runtime_closure.sh
```

See:

```text
CURRENT_SOURCE_STATE.md
PROJECT_STATUS.md
docs/PROJECT_COMPLETENESS_AUDIT.md
```

for the exact distinction between engineered/static contracts and real runtime evidence.

## Environment model

The repository intentionally uses multiple Python environments because the canonical dbt runtime, Dagster + dagster-dbt host runtime, and open-source MetricFlow compatibility runtime currently have different dbt-core compatibility ranges.

```text
requirements-dbt.txt                 canonical dbt 1.12 modeling runtime
requirements-dagster.txt             Dagster host + dagster-dbt compatible dbt 1.11 runtime
requirements-metricflow-compat.txt   local open-source MetricFlow compatibility
requirements-datahub.txt             DataHub governance runtime (isolated sqlglot boundary)
requirements-rag.txt                 knowledge retrieval
requirements-mcp.txt                 MCP runtime
requirements-serving.txt             FastAPI / Trino client
requirements-streaming.txt           Flink / Kafka path
```

Do not collapse these into one environment unless the upstream version constraints are first reconciled. In particular, the Dagster host and DataHub governance runtimes are intentionally isolated because their current dbt integrations require incompatible `sqlglot` versions.

## GitHub CI and dependency locks

GitHub Actions now provides three independent quality gates:

```text
Static quality gate        Python / Shell / YAML / JSON / source-comment contracts
Full contract suite        all repository pytest contracts in a lightweight CI environment
Dependency resolution     10 isolated runtime environments resolved independently
```

The canonical workflows are:

```text
.github/workflows/ci.yml
.github/workflows/dependency-locks.yml
```

Dependency locks are intentionally per-runtime rather than global. The lock policy is frozen in:

```text
requirements/locks/LOCK_POLICY.yml
```

and resolves Python 3.11 / Linux x86_64 dependencies with uv, SHA-256 hashes, and a package-publication cutoff. Generate all locks with:

```bash
./scripts/lock_dependencies.sh all
python scripts/check_dependency_locks.py --require-all
```

If the repository does not yet contain committed full transitive locks, run the **Dependency Locks** workflow on GitHub, review the generated artifact, and commit the resulting `requirements/locks/*.lock.txt` files. CI can bootstrap from an ephemeral hash lock until that first online resolution is committed.

## Engineering boundaries

- Observation ≠ Business Version
- Execution Window ≠ Business Time
- Static Contract ≠ Runtime Observation
- Expected Dataset URN ≠ Resolved Runtime Identity
- `REFERENCE_ONLY` ≠ `SEMANTIC_READY`
- RAG Evidence ≠ Runtime Fact
- Serving Projection ≠ Metric Authority
- Agent Approval ≠ Production Execution

## Source comment standard

Core source follows `docs/SOURCE_COMMENT_STANDARD.md`: comments should explain business logic, relevant syntax/API, input/output, data semantics and the engineering boundary close to the code that owns the responsibility.
