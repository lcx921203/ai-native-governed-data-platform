---
id: commerce.architecture.entity_state_event
title: Entity State Event Model
scope: architecture
domain: modeling
authority: design_decision
owner: data_platform
status: active
tags:
  - entity
  - state
  - event
reviewed_at: 2026-08-19
---

# Entity / State / Event

Entities describe durable business objects, State models describe a current or point-in-time condition, and Event models describe something that occurred. The platform avoids forcing every business object into a current-state table.
