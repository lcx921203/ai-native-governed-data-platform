---
id: commerce.shopify.order_semantics
title: Shopify Order Semantics
scope: business
domain: order_sales
authority: reference
owner: data_platform
status: active
tags:
  - shopify
  - order
reviewed_at: 2026-08-19
---

# Shopify Order Semantics

`created_at` represents order creation time; source update clocks describe source change/observation timing. Fulfillment operational milestones may come from fulfillment/WMS-side events rather than being inferred from order creation/update timestamps. Field meaning must not be used to invent current runtime state.
