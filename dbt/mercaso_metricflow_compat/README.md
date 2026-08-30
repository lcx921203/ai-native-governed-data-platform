# MetricFlow Local Compatibility Project

This directory is **not** the canonical semantic model.

Canonical source of truth:

```text
dbt/mercaso_dbt/
  models/marts/commerce/_commerce_semantic.yml
  models/metrics/*.yml
```

The canonical project uses the latest dbt Semantic Layer YAML spec and is locked to dbt Core 1.12.
At the time this runtime was assembled, `dbt-metricflow==0.13.0` still requires `dbt-core<1.12.0`.
Therefore local `mf` validation runs in this isolated Core 1.11 compatibility project.

`infra/runtime/generate_metricflow_legacy.py` converts the canonical latest spec into the legacy
MetricFlow spec. Do not hand-edit `_generated_semantic_legacy.yml`.

The compatibility generator also maps latest-spec Conversion Metrics into the legacy
`conversion_type_params` structure used by `dbt-metricflow==0.13.0`. Lifecycle conversion
semantics still come only from the canonical `mercaso_dbt` project.

The SQL models in this project are thin views over the canonical Iceberg marts in
`polaris.analytics.*`. This allows MetricFlow to validate/query the same physical business data
without duplicating business SQL logic.
