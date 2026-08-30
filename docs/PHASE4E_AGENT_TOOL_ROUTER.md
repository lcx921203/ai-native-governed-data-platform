# Phase 4E — Agent Tool Router / Intent Planning

## Goal

Phase 4D exposed six governed read tools. Phase 4E adds a deterministic planning layer in
front of those tools so a future LLM does not decide freely which DataHub or runtime APIs to
call.

The first router is deliberately **pre-LLM**. It proves the routing contract before natural
language planning is delegated to a model.

```text
User question
    -> deterministic intent classification
    -> governed target resolution
    -> minimum bounded tool plan
    -> governed read tools
    -> evidence-aware result
```

## Intents

| Intent | Example | Minimum plan |
|---|---|---|
| `METRIC_DEFINITION` | `activity_net_sales 是什么意思？` | `get_metric_context` |
| `ENTITY_CONTEXT` | `订单这个实体是什么？` | `get_entity_context` |
| `DATASET_GOVERNANCE` | `orders 属于哪个业务域？` | `get_dataset_context` |
| `LINEAGE_QUERY` | `orders 的上游是谁？` | `get_lineage_context(max_hops=2)` |
| `RUNTIME_DIAGNOSIS` | `为什么 orders 昨天没更新？` | `get_dataset_context -> get_runtime_context` |
| `METADATA_DISCOVERY` | target is unknown/ambiguous | `search_metadata` only |

## Why deterministic first?

The router owns **tool permission and sequence**, not business definitions. Metric formulas
still come from dbt / MetricFlow; Dataset governance still comes from the governance
contracts; runtime facts still require Dagster/DataHub runtime evidence.

This prevents a future LLM from turning a vague question into arbitrary graph traversal,
SQL, or an unnecessarily large context dump.

## Fail-closed rules

1. Maximum tool calls per plan: `3`.
2. Only the six Phase 4D governed tools are routable.
3. Direct SQL execution requests are blocked.
4. Unknown or ambiguous targets fall back to governed registry search; the router never
   auto-selects a fuzzy result.
5. Lineage is bounded to two hops.
6. Runtime diagnosis stops at `DEFERRED` when real Dagster/DataHub runtime evidence is absent.
7. No DataHub mutation is reachable from this layer.

## Target resolution

`agent/contracts/intent_routing.yml` contains routing vocabulary / aliases. These aliases are
**NLU vocabulary**, not business definitions. They may map Chinese/English phrasing to stable
governed ids, for example:

```text
客单价 -> average_order_value
订单表 -> orders
订单实体 -> order
```

The metric formula or entity relationship is not duplicated in that contract.

## Runtime diagnosis boundary

```text
为什么 orders 昨天没更新？
    -> get_dataset_context("orders")
    -> get_runtime_context("orders")
```

In the current phone-only / no-runtime stage, the second tool returns:

```text
status = DEFERRED
latest_run = null
latest_failure = null
latest_recovery = null
```

The planner is therefore useful now, while real runtime diagnosis remains explicitly
unverified.

## CLI

Plan only:

```bash
PYTHONPATH=. python agent/route_cli.py "activity_net_sales 是什么意思？"
```

Execute the governed read plan:

```bash
PYTHONPATH=. python agent/route_cli.py "为什么 orders 昨天没更新？" --execute
```

## Acceptance

Run:

```bash
./infra/runtime/run_phase4e_agent_router_static.sh
```

This verifies Phase 4A–4E contracts, generated routing examples, bounded plans, fail-closed
SQL handling, and the runtime `DEFERRED` evidence boundary.

## Not implemented yet

Phase 4E does **not** introduce an LLM, free-form SQL, natural-language SQL generation,
write tools, or autonomous multi-agent loops. Those are later concerns. The next layer can
add an LLM as a constrained interpreter above this deterministic contract rather than
replacing the contract.
