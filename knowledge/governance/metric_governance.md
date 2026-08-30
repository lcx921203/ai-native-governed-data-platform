---
id: commerce.governance.metric
title: Metric Governance
scope: governance
domain: semantic
authority: normative
owner: commerce_analytics
status: active
tags:
  - metric
  - governance
reviewed_at: 2026-08-30
---

# Metric Governance

Canonical metric formulas stay in dbt + MetricFlow. Governance controls names, ownership, dimensions, filters, evidence, access and **Metric Version Lifecycle（指标版本生命周期）**; it does not create a second formula source inside Agent contracts or RAG.

## Version lifecycle

Current consumer admission is declared by `metadata/datahub/governance/metric_registry.yml`, which points each governed Metric to one `current_version`.

Append-only version history is declared by `metadata/datahub/governance/metric_lifecycle.yml` and records:

- business version;
- `DRAFT / ACTIVE / DEPRECATED / RETIRED` lifecycle status;
- change type (`BASELINE / NON_BREAKING / BREAKING`);
- effective time boundary;
- superseded version;
- business / technical ownership;
- canonical dbt / MetricFlow definition fingerprint.

The fingerprint is a CI drift guard: changing calculation semantics without creating a new lifecycle version fails closed. Baseline versions intentionally allow unknown historical `effective_from` because the repository does not have trustworthy evidence for the original business launch date of every pre-existing metric.

See `docs/METRIC_VERSION_LIFECYCLE.md` for the V1 → V2 change procedure, Golden Regression rules and evidence boundary.
