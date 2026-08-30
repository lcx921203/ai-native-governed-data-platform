# Governed Commerce Data Agent

This directory contains the governed Agent layer above dbt / MetricFlow, DataHub metadata,
and Dagster operational contracts.

## Current architecture

```text
Natural-language question
    ↓
Deterministic Router
    ↓
Governed Tools
    ├── metadata read tools
    ├── semantic metric query
    ├── dimension-value discovery / resolution
    └── runtime context
    ↓
Evidence-first response envelope
    ↓
Constrained renderer / optional OpenAI provider
```

Multi-turn analysis adds structured state rather than replaying free-form chat history:

```text
Clarification → Analysis Session → Time Comparison → Breakdown / Contribution
```

## Public Tool surface

- `search_metadata`
- `get_entity_context`
- `get_metric_context`
- `get_dataset_context`
- `get_lineage_context`
- `get_runtime_context`
- `query_semantic_metric`
- `query_semantic_metrics`
- `get_dimension_values`
- `resolve_dimension_value`

Arbitrary SQL, raw `where`, generic DataHub graph mutation, and autonomous session mutation
are intentionally not public tools.

## Useful phone-safe / static commands

```bash
PYTHONPATH=. python agent/cli.py metric activity_net_sales
PYTHONPATH=. python agent/route_cli.py "为什么 orders 昨天没更新？" --execute
PYTHONPATH=. python agent/answer_cli.py "activity_net_sales 是什么意思？"
PYTHONPATH=. python agent/query_cli.py "2026-08-05 gross_sales 是多少？" --metrics gross_sales
PYTHONPATH=. python agent/dimension_values_cli.py --metrics gross_sales --dimension store__region
```

Phase 5 closure:

```bash
./infra/runtime/run_phase5_static_closure.sh
```

Real DataHub / Dagster / MetricFlow / Spark / Polaris / OpenAI runtime evidence remains
`DEFERRED` until explicitly enabled and verified.
