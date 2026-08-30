# DataHub Governance Plane

DataHub is the governed discovery and metadata context for the platform. It catalogs business-facing datasets and
extends lineage through orchestration, Serving, BI and API consumption without becoming a second metric authority.

Core governance contracts live in `metadata/datahub/governance/`:

- Domains, owners, tags, glossary and structured properties
- Governed Metric current registry + Metric Version Lifecycle ledger
- dbt Mart governance policy
- Serving governance policy
- BI/API consumer registry

Static generated expectations live in `metadata/datahub/generated/`. Real DataHub evidence belongs only under
`.runtime/evidence/` and is ignored by Git.

## Serving extension

```text
dbt Marts
   ↓
Dagster DataFlow / DataJob
   ↓
Iceberg Serving Dataset
   ├──→ Dashboard
   └──→ OpenAPI Endpoint Dataset
```

`Metric Authority = METRICFLOW` is attached to the Serving Dataset. `Agent Readiness = REFERENCE_ONLY` prevents fixed
consumer projections from replacing the Agent's governed MetricFlow query path.

See `docs/SERVING_GOVERNANCE_AND_LINEAGE.md` for the exact identity and lineage gates.

## Metric version lifecycle

`metric_registry.yml` exposes the current governed Metric version, while `metric_lifecycle.yml` keeps append-only version history and SHA-256 definition fingerprints. CI blocks silent rewrites of an ACTIVE metric definition. Metric formulas remain owned by dbt + MetricFlow.

See `docs/METRIC_VERSION_LIFECYCLE.md`.
