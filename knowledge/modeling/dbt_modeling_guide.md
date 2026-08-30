---
id: commerce.modeling.dbt
title: dbt Modeling Guide
scope: modeling
domain: modeling
authority: normative
owner: data_platform
status: active
tags:
  - dbt
  - modeling
reviewed_at: 2026-08-19
---

# dbt Modeling Guide

Source declares external contracts, Staging is source-conformed normalization, Intermediate performs explicit transformations/current-state selection, and Marts expose stable business grains. Agent complexity must not change those responsibilities.
