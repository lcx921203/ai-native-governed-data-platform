# Dagster Orchestration

Dagster owns the platform's logical-partition delivery lifecycle rather than metric definitions.

```text
Source / Normalize / dbt Assets
            ↓
      Asset Checks
            ↓
       Freshness
            ↓
Structured Failure Evidence
            ↓
     Recovery Policy
```

For fixed BI/API consumption, Dagster also owns the Serving export lifecycle:

```text
MetricFlow governed query
          ↓
serving_daily_export_job
          ↓
Spark materialization
          ↓
Iceberg serving.bi_daily_executive
          ↓
Trino -> BI / FastAPI
```

The Serving job does not contain metric formulas. It invokes a fixed contract in `serving/contracts/`, delegates metric semantics to MetricFlow, and materializes the result as a rebuildable Iceberg projection.

Key concepts used in this project:

- Asset / Materialization
- Daily logical partitions
- Backfill
- Schedule vs Freshness
- Asset Checks
- Step Retry vs Cross-run Recovery
- Failure Evidence and fail-closed recovery
- Fixed Serving export orchestration
