# Phase 4D — Governed Metadata Read Tools

## 1. Why this phase exists

The Agent should not receive unrestricted access to the DataHub graph and should not infer
business semantics from whichever metadata record happens to rank highest in search.
Phase 4D introduces a deliberately small, read-only context surface.

```text
User question
    ↓
Agent
    ↓
Governed tool allowlist
    ├── search_metadata
    ├── get_entity_context
    ├── get_metric_context
    ├── get_dataset_context
    ├── get_lineage_context
    └── get_runtime_context
    ↓
Source-owned metadata
    ├── dbt / MetricFlow  → formulas + entity relationships
    ├── DataHub           → governed Dataset context + runtime lineage
    ├── Git contracts     → labeled static fallback
    └── Dagster Runtime   → run / failure / recovery facts
```

The important boundary is **Read Tool ≠ Source of Truth**. The tool composes context; it does
not become another location where formulas or entity joins are manually redefined.

## 2. Ownership rules

| Context | Source of truth | Static fallback allowed? |
|---|---|---|
| Metric formula | dbt / MetricFlow | Yes, from dbt YAML itself |
| Entity relationship | dbt / MetricFlow | Yes, from semantic YAML itself |
| Domain / Owner / Tag / Glossary / Properties | DataHub after Runtime | Yes, from Git governance contract, explicitly unverified |
| Dataset identity | Exact DataHub identity | Expected URN may be shown, but never treated as resolved |
| Lineage | DataHub Runtime preferred | dbt `ref()` / `source()` static lineage, explicitly labeled |
| Run / Failure / Recovery | Dagster operational metadata in DataHub | **No** |

## 3. Tool contract

`metadata/datahub/contracts/agent_read_contract.yml` forbids:

- arbitrary DataHub graph queries from the LLM;
- raw metadata dumps;
- arbitrary SQL execution;
- fuzzy Dataset runtime binding;
- deriving a metric formula from glossary prose;
- deriving Runtime facts from schedules or static policy.

Lineage is capped at two hops for the first Agent iteration. This keeps context bounded and
makes tool output inspectable.

## 4. Tool behavior

### `get_metric_context("activity_net_sales")`

The glossary supplies the business meaning, but the calculation comes directly from
`dbt/mercaso_dbt/models/metrics/sales.yml`:

```text
sales_before_reversal - sales_reversal_amount
```

The tool recursively resolves the input metrics to their source semantic models and returns
the related governed datasets and entities.

An existing dbt metric that has not been admitted to `metric_registry.yml` is returned as
`BLOCKED`, not silently exposed to the Agent.

### `get_entity_context("order")`

Entity roles are read from `_commerce_semantic.yml`. `order` is primary in `orders` and a
foreign entity in the order-item, payment, refund, and fulfillment semantic models. The
entity registry only maps that semantic entity to governance meaning; it does not duplicate
relationships.

### `get_dataset_context("orders")`

While DataHub Runtime is unavailable, the tool returns Domain, ownership policy, Tags,
Glossary Terms and Structured Properties from the Git contract with:

```text
evidence = STATIC_CONTRACT
identity.status = UNVERIFIED_EXPECTED
runtime_dataset = null
```

Once Phase 4C identity is `RESOLVED` and a DataHub client is available, the same tool can read
the exact Dataset entity by URN. It never switches to fuzzy name search.

### `get_lineage_context("orders")`

Preferred path:

```text
RESOLVED Dataset URN
    → DataHub Lineage SDK
```

Current fallback:

```text
orders.sql
    → ref('int_shopify__orders_canonical')
    → ref('stg_shopify__orders')
```

The fallback is useful for engineering and Agent planning, but it is returned as
`STATIC_CONTRACT`, never `RUNTIME_VERIFIED`.

### `get_runtime_context("orders")`

The static Dagster automation contract may be returned:

```text
Schedule          00:15 UTC
Freshness         01:00 UTC
Budget             45 minutes
Recovery horizon   7 days
```

But static policy cannot answer "did yesterday's run fail?". Therefore current output is:

```text
status          DEFERRED
latest_run      null
latest_failure  null
latest_recovery null
```

Only real Dagster/DataHub operational metadata may populate those fields in a later Runtime
phase.

## 5. DataHub Runtime adapter

`agent/adapters/datahub_sdk.py` uses the official Python SDK lazily. The adapter exposes only:

```text
get_dataset(exact_urn)
get_lineage(exact_urn, direction, max_hops)
```

There are deliberately no create, update, upsert, emit, or generic query methods on the Agent
adapter. Governance writes remain isolated in Phase 4C tooling.

## 6. No LLM yet

Phase 4D is intentionally testable without a model. `agent/cli.py` exercises the exact same
tools that a later Tool Calling / MCP layer will invoke.

Examples:

```bash
PYTHONPATH=. python agent/cli.py metric activity_net_sales
PYTHONPATH=. python agent/cli.py entity order
PYTHONPATH=. python agent/cli.py dataset orders
PYTHONPATH=. python agent/cli.py lineage orders --direction upstream --max-hops 2
PYTHONPATH=. python agent/cli.py runtime orders
```

This separation means Agent behavior can later be tested independently from metadata
correctness.

## 7. Evidence boundary

Phase 4D static acceptance can prove:

- governed tool allowlist;
- formulas come from dbt / MetricFlow;
- semantic relationships come from dbt;
- ungoverned metrics are blocked;
- DataHub runtime binding requires exact resolved identity;
- static lineage is bounded and labeled;
- runtime facts are not invented.

It cannot prove:

- a real DataHub Dataset read;
- a real DataHub lineage traversal;
- a real DataProcessInstance / Dagster Run query;
- end-to-end LLM Tool Calling.

Those remain Runtime / subsequent-phase evidence.
