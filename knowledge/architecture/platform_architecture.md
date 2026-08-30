---
id: commerce.architecture.platform
title: Platform Architecture
scope: architecture
domain: platform
authority: design_decision
owner: data_platform
status: active
tags:
  - architecture
reviewed_at: 2026-08-19
---

# Platform Architecture

The authority planes are intentionally separated: MetricFlow computes governed metrics, DataHub owns metadata/governance context, Dagster observes orchestration/runtime state, and Knowledge RAG answers explanatory/document questions. MCP is an integration protocol, not a fifth data authority.
