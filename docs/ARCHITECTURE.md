# Architecture

## 1. End-to-end architecture

The platform separates **business truth**, **metric authority**, **execution truth**, **governance context**, and **consumer serving** instead of collapsing them into one database or one Agent runtime.

```text
                                   Commerce Sources
                                         │
               ┌─────────────────────────┼──────────────────────────┐
               │                         │                          │
               ▼                         ▼                          ▼
       Shopify Admin API          MySQL Item / Store         Web / App Behavior
          external SaaS               internal OLTP               event stream
               │                         │                          │
    Python GraphQL Extractor           binlog                FastAPI Collector
    updated_at + lookback               │                          │
    cursor + cost throttle              ▼                        Kafka
    API version guard              Flink CDC                      │
               │                  + Flink SQL                    ▼
               │                         │                 PyFlink DataStream
               │                         │             watermark / state / late data
               └──────────────┬──────────┴──────────────┬──────────┘
                              ▼                         ▼
                         Iceberg Raw / Source / Ops / Realtime
                                      │
                                      ▼
                                  dbt Staging
                                      │
                                      ▼
                                dbt Intermediate
                                      │
                                      ▼
                                   dbt Marts
                                      │
                                      ▼
                           Semantic Layer / MetricFlow
                               Metric Authority
                                      │
                    ┌─────────────────┴─────────────────┐
                    │                                   │
                    ▼                                   ▼
          Dynamic semantic query                Fixed Serving Contract
                    │                                   │
                    ▼                                   ▼
             Governed Agent                         Dagster
                                                        │
                                                        ▼
                                             Iceberg Serving Tables
                                                        │
                                                        ▼
                                                       Trino
                                                 Query Serving Engine
                                                   ┌────┴────┐
                                                   ▼         ▼
                                                  BI      FastAPI
                                                             │
                                                             ▼
                                                       Business Apps
```

Architecture diagram artifacts are stored with the source:

```text
docs/architecture/AI_NATIVE_DATA_AGENT.mmd   # editable Mermaid source
docs/architecture/AI_NATIVE_DATA_AGENT.dot   # Graphviz source
docs/architecture/AI_NATIVE_DATA_AGENT.svg   # rendered diagram
```

## 2. Source integration — choose by source semantics

The project intentionally does **not** force every source through one connector:

- SaaS API → observation-based pull;
- internal OLTP → CDC changelog;
- high-frequency behavior → durable event stream + stateful processing.

## 3. Source truth semantics

```text
Shopify API Observation
  Raw keeps repeated observations
  -> Business Key + record_hash decides distinct business versions

MySQL CDC Change
  MySQL binlog exposes INSERT / UPDATE / DELETE changelog
  -> Iceberg v2 current-state tables use business primary key + upsert

Behavior Event
  Raw keeps transport observations
  -> event_id stateful dedup creates canonical event
  -> event-time window uses Watermark / Allowed Lateness / Side Output
```

## 4. Modeling and semantic authority

```text
Iceberg Source
    ↓
dbt Staging
    ↓
dbt Intermediate
    ↓
dbt Marts
    ↓
MetricFlow
```

`dbt Marts` owns stable business facts, states, lifecycle snapshots and entities. MetricFlow owns the metric definitions and valid semantic relationships used by both dynamic Agent analysis and fixed downstream serving.

A fixed BI or API requirement may be physically materialized for latency and interface stability, but the Serving Layer must not define a second metric formula.

## 5. Orchestration and recovery

Dagster owns the delivery lifecycle around a logical business partition:

```text
Logical Partition
      ↓
Asset Graph
      ↓
Materialization / Checks
      ↓
Freshness
      ↓
Structured Failure Evidence
      ↓
Recovery Policy
```

Step retry and cross-run recovery are intentionally separate permissions. Recovery decisions fail closed when structured evidence is insufficient.

## 6. Serving Layer — stable consumption without a second truth

Fixed Dashboard and business API workloads use a rebuildable Serving projection:

```text
MetricFlow
   ↓
Fixed Serving Contract
   ↓
Dagster Export Asset
   ↓
Iceberg Serving Table
   ↓
Trino
   ├── BI / Dashboard
   └── FastAPI -> Business App
```

Responsibilities:

- **MetricFlow** — metric formula, entity relationship, dimension and business-time semantics.
- **Serving Contract** — fixed metric/dimension selection and output schema; no metric formula and no arbitrary SQL.
- **Dagster** — schedule, logical partition, retry/recovery boundary and export orchestration.
- **Iceberg Serving Table** — rebuildable physical projection for stable/high-frequency consumers.
- **Trino** — SQL query-serving engine over Iceberg; it does not own metric definitions.
- **FastAPI** — stable endpoint contract over approved Serving queries; no arbitrary SQL endpoint.
- **BI** — dashboard/report consumer through Trino.
- **Agent** — dynamic governed semantic analysis through MetricFlow rather than through Serving tables.

## 7. Flink fault-tolerance boundary

```text
Kafka / MySQL replayable position
          +
Managed State / Window / Timer
          │
          ▼
      Checkpoint
          │ durable storage
          ▼
    Restore after failure
          │
          ▼
Iceberg checkpoint-aware commit
```

Exactly-once does not mean a record is physically processed once. Replay after the latest completed checkpoint is allowed; the recovered state and sink result must be equivalent to a failure-free execution.

## 8. Governance, knowledge and Agent runtime

```text
MetricFlow ──────────────┐
Dagster runtime truth ───┤
DataHub governance ──────┤──> Governed Agent -> MCP Runtime
Knowledge RAG ───────────┘
```

Each source keeps its own authority:

- MetricFlow → governed metrics;
- Dagster → operational execution evidence;
- DataHub → ownership, lineage, glossary and metadata context;
- Knowledge RAG → design rationale, SOP and troubleshooting knowledge;
- MCP → protocol adapter only; it does not become a new source of truth.

## 9. Storage / compute responsibilities

```text
RustFS       = S3-compatible object storage
Iceberg      = open table format / snapshot / schema evolution
Polaris      = Iceberg REST catalog
Spark        = batch compute and serving-table materialization
Flink        = CDC + stateful streaming compute
Kafka        = durable replayable behavior event log
dbt          = SQL modeling framework
MetricFlow   = metric / semantic authority
Dagster      = orchestration / checks / recovery / fixed serving export
DataHub      = governance metadata context
Trino        = interactive SQL query serving over Iceberg
FastAPI      = stable business API serving surface
```

## 10. Why Entity-style Marts

The project does not pre-build a large set of rigid DWS combinations such as:

```text
dws_day_region_category_sales
dws_month_store_brand_sales
```

Instead it keeps stable entities/events such as orders, order_items, stores, items and behavior events, then lets the governed Semantic Graph combine valid dimensions and metrics. Physical Serving tables are added only when a known consumer benefits from a stable contract or precomputation.
