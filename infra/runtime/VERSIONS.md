# Runtime Version Lock

This is the first **runtime-validation baseline** before Dagster is introduced.
The goal is reproducibility, not chasing the newest version of every component.

| Component | Locked version | Why this baseline |
|---|---:|---|
| Apache Polaris | `1.7.0` | Current Polaris release (2026-08-02) with a published `apache/polaris:1.7.0` image. |
| RustFS | `1.0.0-beta.8` | Version used by the Polaris 1.7.0 RustFS guide's compose example. |
| Apache Spark | `3.5.6-java17` | Base image used by the Polaris 1.7.0 Spark guide. |
| Apache Iceberg | `1.10.1` | Runtime version used by the Polaris RustFS/Quickstart Spark commands. |
| Scala binary | `2.12` | Matches `iceberg-spark-runtime-3.5_2.12`. |

## Important

- This lock is for the local learning/demo environment.
- `root:s3cr3t` and `rustfsadmin/rustfsadmin` are intentionally simple local-only credentials.
- We do **not** call the stack validated until Docker actually starts and the smoke tests pass.
- Once this baseline works, do not upgrade individual components casually; upgrade as a tested set.

## dbt modeling runtime

Canonical project:

| Component | Version | Purpose |
|---|---:|---|
| dbt Core | 1.12.2 | Parses latest Semantic Layer YAML and executes dbt DAG |
| dbt-spark | 1.11.0 | Spark Thrift adapter |

Local MetricFlow compatibility environment:

| Component | Version | Purpose |
|---|---:|---|
| dbt Core | 1.11.13 | Satisfies current dbt-metricflow upper bound |
| dbt-spark | 1.11.0 | Queries the same Spark/Polaris runtime |
| dbt-metricflow | 0.13.0 | Provides local `mf` CLI |

The two dbt environments are intentionally isolated. The compatibility project is generated from
the canonical latest-spec semantic YAML and will be removed once local `dbt-metricflow` supports
Core 1.12 directly.


## Streaming ingestion baseline

| Component | Version | Purpose |
|---|---:|---|
| Apache Flink | `1.20.5` | PyFlink DataStream / Flink SQL execution baseline |
| Apache Flink CDC | `3.6.0` | MySQL snapshot + binlog capture; supports Flink 1.20.x |
| Iceberg Flink runtime | `1.10.1` | Checkpoint-aware Iceberg streaming sink aligned with the existing Iceberg baseline |

These are source/runtime targets. They are **not** marked live-validated until the streaming cluster and failure drill actually run.
