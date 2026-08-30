# Serving Layer + Trino Source Extension Manifest

Date: 2026-08-21

## Architecture contract

```text
MetricFlow
  -> Fixed Serving Contract
  -> Dagster Export
  -> Spark / Iceberg Serving Table
  -> Trino
  -> BI / FastAPI

Agent dynamic analytics
  -> MetricFlow directly
```

MetricFlow remains the only metric-definition authority. Serving artifacts are rebuildable consumer projections.

## Added source

```text
serving/
  contracts/bi_daily_executive.yml
  contracts.py
  exporter.py
  export_cli.py
  jobs/materialize_export.py
  api/
  bi/

infra/trino/
infra/serving/Dockerfile
requirements-serving.txt

docs/SERVING_LAYER_AND_TRINO.md
docs/architecture/AI_NATIVE_DATA_AGENT.mmd
docs/architecture/AI_NATIVE_DATA_AGENT.dot
docs/architecture/AI_NATIVE_DATA_AGENT.svg

orchestration/dagster/commerce_dagster/assets/serving.py
orchestration/dagster/commerce_dagster/serving_readiness.py

tests/test_serving_layer_contract.py
infra/runtime/run_serving_static.sh
infra/runtime/run_serving_runtime.sh
```

## Modified integration points

- `docker-compose.yml` — Trino + Serving API services.
- `.env.example` — Trino/Serving configuration and fail-closed export gate.
- Dagster `definitions.py`, `jobs.py`, `schedules.py`, `automation_policy.py` — Serving Asset/Job/Schedule registration.
- `README.md`, `docs/ARCHITECTURE.md`, Dagster README and source-state documents — current architecture and responsibility boundaries.
- `docs/SOURCE_COMMENT_STANDARD.md` — Serving/Trino source-comment standard.

## Static acceptance

- Serving contract / API / topology tests: **8 passed**.
- Whole repository: **391 passed**.
- Python compile: PASS.
- Serving shell syntax: PASS.
- Docker/Trino/Dagster real runtime: NOT EXECUTED in the current environment.


## DataHub governance / consumer lineage extension

Added:

```text
metadata/datahub/governance/serving_policy.yml
metadata/datahub/governance/consumer_registry.yml
metadata/datahub/generated/serving_governance_projection.json
metadata/datahub/recipes/serving_api_openapi.yml
metadata/datahub/tools/build_serving_governance_projection.py
metadata/datahub/tools/resolve_serving_consumer_identities.py
metadata/datahub/tools/serving_runtime.py
serving/api/export_openapi.py
serving/api/openapi.json
infra/runtime/run_serving_governance_static.sh
infra/runtime/run_serving_governance_runtime.sh
tests/test_serving_datahub_governance.py
docs/SERVING_GOVERNANCE_AND_LINEAGE.md
```

DataHub entity model:

```text
Iceberg Marts -> Dagster DataFlow/DataJob -> Iceberg Serving Dataset -> BI Dashboard
                                                          \-> OpenAPI Endpoint Dataset
```

Static governance tests: **6 passed**. Combined Serving tests: **14 passed**. Whole repository: **391 passed**. Real DataHub writes and final consumer-lineage re-query remain runtime-gated and unexecuted in this environment.
