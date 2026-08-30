---
id: commerce.runbook.datahub_ingestion
title: DataHub Ingestion Runbook
scope: runbook
domain: metadata
authority: runbook
owner: data_platform
status: active
tags:
  - datahub
  - ingestion
reviewed_at: 2026-08-19
---

# DataHub Ingestion Runbook

Ingest physical Iceberg assets first, then dbt metadata/lineage, bootstrap governance definitions, resolve exact dataset identities, apply governance projection and finally re-query DataHub to verify observed state.
