# Phase 7A — Real Runtime Bootstrap

Status: `ENGINEERED_RUNTIME_EXECUTION_DEFERRED`

Phase 7A restores the workstation execution path without turning static readiness into runtime evidence.

Core order: **RustFS → Polaris → Iceberg/Spark → dbt → MetricFlow → Dagster**. DataHub is intentionally a separate metadata-plane bootstrap.

## Read-only readiness scan

```bash
./infra/runtime/run_phase7a_preflight.sh
```

A preflight PASS means `READY_FOR_BOOTSTRAP`, never `RUNTIME_VERIFIED`.

## Real workstation gate

```bash
cp .env.example .env
PHASE7A_ALLOW_RUNTIME_BOOTSTRAP=true ./infra/runtime/run_phase7a_core_bootstrap.sh
```

The runner uses isolated `.venv-dbt`, `.venv-mf`, and `.venv-dagster` environments. Only the successful real service/query acceptance path may write `.runtime/evidence/phase7a/core_runtime.json` with `runtime_verified=true` and `RUNTIME_BOOTSTRAP_VERIFIED`.

`.runtime/` is generated evidence and is gitignored. Source contracts never upgrade themselves.
