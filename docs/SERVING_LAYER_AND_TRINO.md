# Serving Layer + Trino

## 1. Why this layer exists

Semantic Layer solves **metric meaning**. Fixed dashboards and business APIs additionally need predictable
latency, stable schemas and consumer-specific interfaces. Those are Serving responsibilities.

The project therefore separates:

```text
Metric definition       -> MetricFlow
Fixed materialization   -> Dagster + Spark/Iceberg
Interactive serving     -> Trino
Business interface      -> BI / FastAPI
Dynamic analytics       -> Agent + MetricFlow
```

## 2. One Metric Authority

A fixed report is not allowed to re-implement `net_sales`, `AOV` or `order_count` in ADS-style SQL.
`serving/contracts/bi_daily_executive.yml` references the governed MetricFlow names. Dagster executes that
fixed query for one daily partition and materializes the returned columns.

The Serving table is therefore a **rebuildable projection/cache**. Deleting it must not delete the business
meaning of any metric.

## 3. Daily export flow

Before MetricFlow runs, the Serving Asset checks the exact daily Materialization Evidence declared by the contract
(`orders`, `order_items`, `refund_items`). Missing upstream partitions fail closed, so a fixed Dashboard cannot publish
a partial business day merely because the export schedule fired.

```text
Dagster partition YYYY-MM-DD
        |
        v
Serving Contract
        |
        v
MetricFlow query (governed metrics + dimensions)
        |
        v
.runtime/serving/.../metricflow.csv
        |
        v
Spark explicit casts + validation
        |
        v
Iceberg WriterV2 exact-partition overwrite(filter)
        |
        v
polaris.serving.bi_daily_executive
```

`overwrite(filter)` is chosen over a `DELETE + INSERT` pair so a rerun creates one Iceberg snapshot that
replaces the exact exported business-day partition. The explicit filter also clears stale rows when a valid rerun returns zero rows.

## 4. Trino responsibility

Trino reads Iceberg through Polaris and exposes SQL/JDBC/DBAPI access to consumers. It does not own:

- metric formula;
- dbt modeling;
- Dagster recovery;
- DataHub ownership/lineage;
- Agent authority.

For local runtime the project pins Trino 483 and configures the Iceberg REST catalog in
`infra/trino/catalog/iceberg.properties`.

## 5. BI and API

BI can connect to Trino directly:

```text
BI -> Trino -> iceberg.serving.bi_daily_executive
```

Business systems use fixed FastAPI endpoints:

```text
GET /api/v1/executive/daily
GET /api/v1/regions/{region}/daily
```

The API has no arbitrary SQL endpoint and does not accept caller-defined metrics. Dynamic combinations remain
an Agent/Semantic-Layer concern.

## 6. DataHub governance across the consumption boundary

Serving does not end at the Iceberg table. DataHub extends the metadata graph across the fixed consumption path:

```text
orders / order_items / refund_items
              ↓
      Dagster Serving DataJob
              ↓
  bi_daily_executive (Iceberg)
         ┌────┴────┐
         ↓         ↓
   BI Dashboard   FastAPI Endpoint
```

The Serving Dataset is tagged `metricflow-governed`, its structured `Metric Authority` is `METRICFLOW`, and its Agent
Readiness is `REFERENCE_ONLY`. API endpoint identity binding remains exact-only after OpenAPI ingestion; guessed or fuzzy
URN binding is forbidden.
