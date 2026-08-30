# Dependency Locks

This directory is the canonical location for **fully resolved, hash-pinned** Python dependency locks.

## Why there are multiple locks

The repository intentionally uses separate Python environments. In particular:

- canonical dbt / Dagster uses dbt-core 1.12.x;
- the current open-source MetricFlow compatibility runtime uses dbt-core 1.11.x;
- DataHub, RAG, MCP, Serving, and Streaming each have their own dependency surface.

Merging them into one environment would hide real compatibility boundaries and make upgrades harder to audit.

## Lock target

`LOCK_POLICY.yml` freezes:

- Python: 3.11
- Platform: Linux x86_64
- Resolver: uv 0.12.1
- Package publication cutoff: 2026-08-21T00:00:00Z
- Hashes: required

A generated lock is named:

```text
<component>-py311-linux.lock.txt
```

For example:

```text
serving-py311-linux.lock.txt
metricflow-compat-py311-linux.lock.txt
ci-py311-linux.lock.txt
```

## Generate locks locally

Requires network access to the configured Python package index:

```bash
./scripts/lock_dependencies.sh all
python scripts/check_dependency_locks.py --require-all
```

Generate only one component:

```bash
./scripts/lock_dependencies.sh serving
```

## Generate locks on GitHub

Run the **Dependency Locks** workflow manually. It resolves all ten environments and uploads the generated `requirements/locks/*.lock.txt` files as one artifact. Review those files and commit them to the repository.

## CI behavior before and after locks are committed

- Before a lock exists, CI resolves an ephemeral hash lock using the same frozen policy and installs from that file.
- After a committed lock exists, CI installs the committed lock and separately checks that dependency resolution still succeeds.

This keeps the repository usable before the first online lock refresh while making the steady state fully reproducible.
