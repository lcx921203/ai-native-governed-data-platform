---
id: commerce.runbook.dagster_recovery
title: Dagster Recovery Runbook
scope: runbook
domain: operations
authority: runbook
owner: data_platform
status: active
tags:
  - dagster
  - recovery
reviewed_at: 2026-08-19
---

# Dagster Recovery Runbook

Recovery decisions use exact-partition materialization truth, active-run ownership, structured failure classification and bounded retry/recovery policy. A policy decision is not evidence that a recovery run actually happened.
