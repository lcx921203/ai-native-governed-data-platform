# Phase 4B — Metadata Identity & Governance Model

## Goal

Phase 4A established where metadata comes from. Phase 4B establishes how the same
metadata is governed so a future Data Agent can distinguish business meaning,
technical labels, ownership, and trust level without inventing a second semantic
model.

## Why these objects are separate

```text
Domain
  -> where this asset belongs organizationally

Glossary Term
  -> what a business concept means

Owner
  -> who is accountable

Tag
  -> informal search/discovery label

Structured Property
  -> typed governed classification / policy value

Entity / Metric Registry
  -> projection from dbt / MetricFlow identities into governance context
```

DataHub currently allows one Domain assignment per asset, while glossary terms and
tags are multi-valued. The project therefore uses a `Commerce` parent domain with
leaf process domains for consumer marts, and uses glossary terms for cross-cutting
concepts such as `Order`, `Refund`, `Business Time`, and `Activity Net Sales`.

## Domain model

```text
Commerce
├── Order & Sales
├── Payments
├── Refunds
├── Fulfillment
└── Commerce Reference
```

A mart is assigned to one leaf domain when possible. Cross-cutting concepts are not
modeled as multiple domains; they live in the glossary.

## Glossary ownership

The glossary is Git-managed and uses explicit stable IDs. It intentionally does not
own dbt formulas or semantic relationships.

```text
dbt / MetricFlow
  = formula + entity relationship truth

Business Glossary
  = controlled business definition and discoverability
```

Examples:

```text
commerce-entity-order
commerce-metric-average-order-value
data-time-business-time
data-model-business-version
```

## Tags vs governed classification

Tags stay informal:

```text
source-shopify
layer-mart
semantic-enabled
daily-partitioned
```

Governed classification is typed instead:

```text
commerce.governance.dataClassification
commerce.governance.criticality
commerce.governance.agentReadiness
```

This prevents `INTERNAL`, `CONFIDENTIAL`, or `RUNTIME_VERIFIED` from becoming
uncontrolled free-form tags.

## Agent readiness

`agentReadiness` is deliberately more conservative than `semantic-enabled`.

```text
REFERENCE_ONLY
  -> context can be shown, but should not be the primary answer source

SEMANTIC_READY
  -> dbt / MetricFlow governed; real runtime evidence may still be deferred

RUNTIME_VERIFIED
  -> required real runtime evidence has been verified

BLOCKED
  -> agent must not use as an analytical answer source
```

Current marts remain `SEMANTIC_READY`, not `RUNTIME_VERIFIED`, because Dagster,
dbt, Spark, and DataHub real runtime acceptance is still deferred.

## Identity boundary

Phase 4B does **not** guess DataHub Dataset URNs. Governance policy is keyed by
canonical dbt model names. After Iceberg and dbt ingestion run against a real DataHub
instance, a later projection step will resolve those canonical model identities to
actual DataHub URNs and then apply Domain / Owner / Term / Tag / Structured Property
metadata.

```text
Current phase
canonical dbt model name
  -> governance policy

Later runtime projection
canonical dbt model name
  -> query DataHub
  -> actual Dataset URN
  -> apply governance metadata
```

This protects the single-identity rule established in Phase 4A.

## Static evidence

Run:

```bash
./infra/runtime/run_phase4b_governance_static.sh
```

This validates references between dbt semantic entities, MetricFlow metrics,
glossary IDs, domains, owners, tags, and structured property values. It does not
prove that DataHub GMS accepted these objects or that governance metadata has been
projected onto real datasets.
