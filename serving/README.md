# Serving Layer — BI / API consumption without a second metric truth

Serving Layer solves a different problem from dbt Marts and MetricFlow:

- **Marts** own stable business facts / states / entities.
- **MetricFlow** owns metric meaning and valid dimensional combinations.
- **Serving** owns fixed, high-frequency consumer projections and stable interfaces.
- **Trino** is the read/query engine over Iceberg Serving tables.

## Runtime path

```text
                 dbt Marts
                     │
                     ▼
                 MetricFlow
               Metric Authority
                     │
          ┌──────────┴──────────┐
          │                     │
          ▼                     ▼
   Dynamic Semantic       Fixed Serving Contract
       Query                     │
          │                      ▼
        Agent                  Dagster
                                 │
                         MetricFlow Export
                                 │
                                 ▼
                          Iceberg Serving
                                 │
                                 ▼
                               Trino
                         ┌───────┴────────┐
                         ▼                ▼
                        BI              FastAPI
                                          │
                                          ▼
                                   Business Apps
```

The key rule is **one Metric Authority, multiple consumption paths**. A Serving table may be dropped and
rebuilt from the governed query contract. It must never be the only place where a metric formula exists.

## Contract

`contracts/bi_daily_executive.yml` declares:

- governed metrics;
- governed group-by dimensions;
- stable target column names/types;
- Iceberg target table / physical partition;
- consumer types (`bi`, `api`).

It intentionally has no SQL/formula field.

## Materialization

Dagster runs the fixed MetricFlow query for one logical daily partition. MetricFlow produces a CSV artifact;
`serving/jobs/materialize_export.py` casts the declared columns and uses Iceberg WriterV2
`overwritePartitions()` so rerunning the same day replaces only that day in one Iceberg snapshot.

## API

FastAPI only reads `iceberg.serving.bi_daily_executive` through Trino. Endpoints are fixed contracts rather
than arbitrary analytics APIs:

```text
GET /api/v1/executive/daily?business_date=2026-08-20
GET /api/v1/regions/West/daily?start_date=2026-08-01&end_date=2026-08-20
```

Dynamic questions such as “Net Sales by any valid dimension combination” stay on the Agent → MetricFlow path.

## DataHub governance and consumer lineage

The Serving table is governed in DataHub as a rebuildable Dataset with `Metric Authority = METRICFLOW` and
`Agent Readiness = REFERENCE_ONLY`. Dagster Serving Export is represented as a DataFlow/DataJob between the Mart inputs
and the Serving Dataset. BI is represented as downstream Dashboard lineage; FastAPI business endpoints are ingested from
the generated OpenAPI contract and linked only after exact endpoint Dataset identities are verified.

See `docs/SERVING_GOVERNANCE_AND_LINEAGE.md`.
