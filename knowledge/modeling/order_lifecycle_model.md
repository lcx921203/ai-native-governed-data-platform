---
id: commerce.modeling.order_lifecycle
title: Order Lifecycle Modeling
scope: modeling
domain: order_sales
authority: design_decision
owner: data_platform
status: active
tags:
  - order
  - lifecycle
reviewed_at: 2026-08-19
---

# Order Lifecycle Modeling

## Why not current state only

A current table answers what the order looks like now, but it cannot by itself preserve when key business milestones happened. Lifecycle analysis therefore keeps transaction/event facts and cumulative milestone semantics where appropriate.

## Cumulative snapshot rationale

A cumulative snapshot can place created, paid, picked, shipped, delivered and completed milestones on one order lifecycle record when those timestamps are truly observed from authoritative sources.

## Current source implementation

The dbt source tree now contains `models/marts/commerce/order_lifecycle_snapshot.sql`.
Its grain is one row per `order_id`. It recomputes an affected Order when the Order itself,
a Transaction, Refund, Fulfillment, or FulfillmentEvent changes inside the execution window,
then MERGEs the complete lifecycle snapshot by `order_id`.

Milestones are limited to timestamps that are explicitly available from current source contracts.
`picked_at` is intentionally absent because the current Shopify source does not provide an authoritative
picking milestone. `first_paid_at` requires a successful `CAPTURE` or `SALE`; authorization alone is not payment.

Status: **SOURCE DEFINED / STATIC TESTED; REAL DBT + SPARK + ICEBERG RUNTIME DEFERRED**.
