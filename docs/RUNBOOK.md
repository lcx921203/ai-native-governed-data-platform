# Runtime Validation Runbook

Dagster is deliberately **not** introduced until the storage/catalog/compute/modeling/semantic path is proven.

## Current validation order

```text
RustFS
  -> Polaris
  -> Spark + Iceberg
  -> Raw Iceberg
  -> Structured Source MERGE
  -> canonical dbt Core 1.12 build/tests
  -> MetricFlow local compatibility queries
  -> Dagster
```

## One-command full validation

From the project root:

```bash
cp .env.example .env
bash infra/runtime/run_full_pre_dagster_validation.sh
```

This performs three independent acceptance stages.

### Stage A — infrastructure + lakehouse

```bash
bash infra/runtime/run_pre_dagster_validation.sh
```

The script intentionally loads the same Shopify fixtures **twice** and runs Normalize **twice**.
Expected semantics:

- Raw observations grow because Raw is append-only.
- Structured Source row counts do not grow for identical business content.
- `business_key + record_hash` remains unique.

### Stage B — canonical dbt build/tests

```bash
bash infra/runtime/run_dbt_validation.sh
```

Canonical versions:

```text
dbt-core 1.12.2
dbt-spark 1.11.0
```

Acceptance:

- `dbt debug`
- `dbt parse`
- `target/semantic_manifest.json` exists
- `dbt build --full-refresh`
- explicit `dbt test`
- metrics are visible via `dbt ls --resource-type metric`
- `dbt show --select order_items` succeeds

### Stage C — MetricFlow local queries

```bash
bash infra/runtime/run_metricflow_validation.sh
```

There is a temporary upstream package conflict: current `dbt-metricflow 0.13.0` requires
`dbt-core<1.12`, while the canonical project intentionally uses the latest Core 1.12 semantic YAML.
The script therefore creates an isolated Core 1.11 compatibility environment, generates legacy
semantic YAML from the canonical definition, and queries thin views over the same canonical marts.

Acceptance includes:

```text
mf validate-configs
mf health-checks
mf list entities / metrics / dimensions
Gross Sales by Item Category                 -> must succeed
Gross Sales by Store Region                  -> must succeed (two-hop semantic path)
Average Order Value by Store Region          -> must succeed
Activity Net Sales by metric_time__day       -> must succeed
Order Count by Item Category                 -> must FAIL (fanout guard)
```

The negative query is intentional: it proves Semantic Join Safety, not just SQL generation.

## Useful endpoints

- RustFS S3 API: `http://localhost:9000`
- RustFS Console: `http://localhost:9001`
- Polaris Iceberg REST: `http://localhost:8181/api/catalog`
- Polaris Health: `http://localhost:8182/q/health`
- Spark Thrift Server: `localhost:10000`

## Manual Spark SQL

```bash
bash infra/spark/scripts/spark-sql.sh
```

Then:

```sql
SHOW CATALOGS;
USE polaris;
SHOW NAMESPACES;
```

## What is not validated in this repository environment

The ChatGPT execution container used to generate this project has no Docker daemon and cannot reach
PyPI from the local container runtime. Python/YAML/Shell/semantic mapping are statically validated;
the actual end-to-end PASS must be produced on a machine with Docker and Internet access for the
first Python dependency installation.
