---
id: commerce.runbook.dbt_failure
title: dbt Failure Runbook
scope: runbook
domain: operations
authority: runbook
owner: data_platform
status: active
tags:
  - dbt
  - failure
reviewed_at: 2026-08-19
---

# dbt Failure Runbook

1. Identify the exact failing model/test from structured evidence.
2. Confirm the affected partition and upstream source freshness.
3. Compare source → staging → intermediate → mart contracts.
4. Re-read the existing Dagster recovery policy before any replay/backfill action.

This runbook advises investigation; it does not authorize execution.
