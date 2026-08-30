---
id: commerce.architecture.semantic_layer
title: Semantic Layer Design
scope: architecture
domain: semantic
authority: design_decision
owner: data_platform
status: active
tags:
  - metricflow
  - semantic
reviewed_at: 2026-08-19
---

# Semantic Layer Design

MetricFlow owns metric definitions and safe entity/dimension paths so business analysis can combine governed dimensions without copying formulas into the Agent. Explain-before-query is used to validate semantic reachability before numeric execution.
