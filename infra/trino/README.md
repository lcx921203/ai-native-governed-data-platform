# Trino — Lakehouse Serving Query Engine

This project uses Trino as a **query/serving compute layer**, not as a new metric or storage authority.

```text
Iceberg Serving Table -> Trino -> BI / FastAPI
```

Local catalog configuration lives in `infra/trino/catalog/iceberg.properties` and uses:

- Iceberg connector;
- Polaris REST Catalog;
- RustFS S3-compatible object storage;
- native Trino S3 file system;
- environment-variable based secrets.

The container is pinned to `trinodb/trino:483` for the current runtime baseline. Trino's official 483
documentation confirms REST Iceberg catalogs and the native S3 properties used here.

## Smoke checks

```bash
docker compose up -d rustfs bucket-setup polaris polaris-setup trino
docker compose exec -T trino trino --execute "SHOW CATALOGS"
docker compose exec -T trino trino --execute "SHOW SCHEMAS FROM iceberg"
docker compose exec -T trino trino --execute "SELECT * FROM iceberg.serving.bi_daily_executive LIMIT 10"
```
