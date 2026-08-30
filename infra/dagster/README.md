# Local Dagster control plane

This directory is used as `DAGSTER_HOME` for Phase 3C runtime acceptance.
The host process is the control plane; the existing Docker Compose stack remains the
Spark / Polaris / RustFS data plane.

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
```

`dagster.yaml` enables OSS freshness evaluation and persists local Dagster instance
state under this directory.
