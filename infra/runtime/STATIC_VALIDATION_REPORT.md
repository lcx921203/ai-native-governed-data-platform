# Static Validation Report

Generated before the first real Docker/dbt/MetricFlow runtime execution.

## Passed in the project-generation environment

- Python source compilation: PASS
- Shell syntax (`bash -n`): PASS
- YAML parse: PASS
- JSON parse: PASS
- Shopify fixture semantic validation: PASS
- dbt `ref()` target resolution: PASS
- dbt `source()` target resolution: PASS
- Canonical latest-spec → generated legacy semantic model names: PASS
- Canonical latest-spec → generated legacy metric names: PASS
- Legacy measure aggregation enum check: PASS

Current semantic inventory:

```text
semantic models: 10
measures in local legacy compatibility spec: 22
metrics: 25
```

Fixture inventory:

```text
orders                   5
order_items              5
discount_allocations     1
transactions             7
refunds                   1
refund_items              1
refund_transactions       1
fulfillments              1
fulfillment_items         1
fulfillment_events        2
```

## Not claimed as passed yet

This environment has no Docker daemon and cannot perform the real dependency installation/runtime.
The following remain pending until executed on a Docker-capable machine:

- RustFS / Polaris / Spark / Iceberg runtime
- Raw ingestion + Structured Source MERGE
- repeated Normalize idempotency assertion
- dbt Core 1.12 parse/build/tests
- MetricFlow validation and queries
- fanout negative query rejection

Run all of them with:

```bash
cp .env.example .env
bash infra/runtime/run_full_pre_dagster_validation.sh
```
