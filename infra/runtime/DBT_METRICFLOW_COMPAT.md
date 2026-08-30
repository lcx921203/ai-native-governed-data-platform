# dbt / MetricFlow Runtime Compatibility

## Canonical dbt environment

```text
dbt-core 1.12.2
dbt-spark 1.11.0
```

The canonical `mercaso_dbt` project uses the latest Semantic Layer YAML spec.

## Why MetricFlow uses a second environment

As of 2026-08-14, `dbt-metricflow 0.13.0` pins `dbt-core >=1.10.4,<1.12.0`, while the latest
Semantic Layer YAML spec is supported by dbt Core 1.12. Installing both in one Python environment
therefore fails dependency resolution.

For local runtime proof only, this project uses:

```text
dbt-core 1.11.13
dbt-spark 1.11.0
dbt-metricflow 0.13.0
```

and generates a legacy Semantic Layer YAML from the canonical latest-spec YAML.

This is an upstream compatibility bridge, not a second source of business truth. Once an official
`dbt-metricflow` release supports Core 1.12 latest-spec projects directly, remove the compatibility
project and run `mf` against `dbt/mercaso_dbt` itself.
