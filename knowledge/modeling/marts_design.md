---
id: commerce.modeling.marts
title: Commerce Marts Design
scope: modeling
domain: commerce
authority: explanatory
owner: data_platform
status: active
tags:
  - marts
  - grain
reviewed_at: 2026-08-19
---

# Commerce Marts Design

Orders, OrderItems, Payments, Refunds and Fulfillments preserve independent grains. Multiple 1:N processes should not be flattened into one uncontrolled super-wide table because fan-out can duplicate amounts and quantities.
