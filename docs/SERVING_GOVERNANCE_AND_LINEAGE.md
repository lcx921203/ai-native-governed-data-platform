# Serving Governance & Consumer Lineage

## Purpose

The Serving extension adds fixed BI and API consumption without creating a second metric authority. DataHub therefore
needs to govern not only the dbt marts, but also the materialized Serving Dataset and its downstream consumers.

The metadata graph is modeled as:

```text
Iceberg Marts
    │
    ▼
Dagster DataFlow / DataJob
    │
    ▼
Iceberg Serving Dataset
    ├──────────────► BI Dashboard
    │
    └──────────────► FastAPI Endpoint Datasets
```

MetricFlow remains outside the physical Dataset lineage as the metric-definition authority. The Serving governance
projection records the exact MetricFlow metric dependencies separately, while physical lineage follows the datasets and
execution job that actually materialize the projection.

## Serving Dataset governance

`metadata/datahub/governance/serving_policy.yml` owns the expected governance state for
`commerce_polaris.serving.bi_daily_executive`:

- Domain: `commerce-order-sales`
- Business owner: `commerce-analytics`
- Technical owner: `data-platform`
- Tags: `layer-serving`, `metricflow-governed`, `consumer-bi`, `consumer-api`, `daily-partitioned`
- Metric Authority: `METRICFLOW`
- Serving Role: `SHARED_BI_API_PROJECTION`
- Agent Readiness: `REFERENCE_ONLY`

`REFERENCE_ONLY` is deliberate. The Agent still queries MetricFlow for governed analytics; it does not bypass the
semantic layer by treating a fixed BI cache as the primary analytical truth.

## Export job lineage

The Dagster Serving Export is represented as DataHub `DataFlow` / `DataJob` metadata. The job consumes exact Iceberg
Mart identities (`orders`, `order_items`, `refund_items`) and produces the exact Serving Dataset identity.

This expresses execution lineage without pretending that the Serving table owns metric formulas.

## BI lineage

`metadata/datahub/governance/consumer_registry.yml` declares a repository-owned logical dashboard contract named
`executive_daily`. It consumes only the Serving Dataset.

The logical platform id `commerce_bi` is intentionally not presented as an external BI integration. When a real BI
platform (for example Superset, Tableau or Power BI) is connected, native DataHub ingestion should replace this logical
Dashboard URN with the real Dashboard entity while preserving the same Dataset → Dashboard lineage.

## API metadata and lineage

FastAPI exports a deterministic OpenAPI document at `serving/api/openapi.json`. DataHub's OpenAPI ingestion recipe is
`metadata/datahub/recipes/serving_api_openapi.yml` and excludes health endpoints.

The governed business endpoints are:

```text
GET /api/v1/executive/daily
GET /api/v1/regions/{region}/daily
```

The repository does **not** guess their DataHub Dataset URNs. After real OpenAPI ingestion, the operator records the two
exact endpoint Dataset URNs with `resolve_serving_consumer_identities.py`. The resolver verifies entity existence plus
endpoint path/method evidence before it writes runtime-only identity evidence under `.runtime/`.

Only then may `serving_runtime.py apply-api-lineage` add:

```text
Iceberg Serving Dataset -> OpenAPI Endpoint Dataset
```

## Runtime gates

All DataHub mutations remain fail closed:

```text
SERVING_GOVERNANCE_ALLOW_DATAHUB_WRITE=false
SERVING_GOVERNANCE_ALLOW_LINEAGE_WRITE=false
SERVING_GOVERNANCE_ALLOW_CONSUMER_WRITE=false
```

Static source and generated projections are not Runtime evidence. Exact DataHub identities and final metadata re-query
are required before a real integration can be marked verified.
