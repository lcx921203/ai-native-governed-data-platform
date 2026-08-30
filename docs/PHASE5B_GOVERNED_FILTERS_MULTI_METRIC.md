# Phase 5B — Governed Dimension Filters + Multi-Metric Query

## Goal

Phase 5A proved the first numeric Agent query surface for one governed MetricFlow metric and an explicit time range. Phase 5B extends that surface to the smallest useful business-analysis shape:

```text
explicit time range
+ 1..3 governed metrics
+ 0..2 governed group-bys
+ 0..2 structured governed dimension filters
→ MetricFlow explain
→ MetricFlow query
→ runtime-verified numeric evidence
```

It deliberately does **not** expose SQL or a raw `where` string.

Example target question:

```text
2026-08-01 到 2026-08-05，美国西部地区，按天看
Gross Sales、Activity Net Sales、Average Order Value
```

The deterministic plan becomes:

```text
metrics:
  - gross_sales
  - activity_net_sales
  - average_order_value

time:
  2026-08-01 .. 2026-08-05

group_by:
  - metric_time__day

filters:
  - store__country EQ US
  - store__region EQ West
```

The executor is allowed to construct MetricFlow filter expressions only from that structured plan:

```text
--where "{{ Dimension('store__country') }} = 'US'"
--where "{{ Dimension('store__region') }} = 'West'"
```

A caller-provided expression such as `where region='west'` or `region=west` is blocked before MetricFlow execution.

## Why multiple metrics share one query

MetricFlow supports querying multiple metrics in one semantic query by passing a comma-separated metric list. Keeping related metrics in one query has two important benefits for an Agent:

1. one shared time/filter/grouping contract;
2. MetricFlow validates whether the complete metric + dimension graph is semantically executable.

Phase 5B caps the metric set at three. It does not split an invalid joint query into independent queries because doing so could silently return results with different semantic grains or filters.

## Filter ownership

Filter **dimension identity** comes from the dbt Semantic Model. Phase 5B currently allows only master-data dimensions:

```text
store__region
store__state
store__country
item__brand
item__category
item__subcategory
```

Filter **canonical demo values** come from the repo-managed master seeds:

```text
dbt/mercaso_dbt/seeds/master/seed_stores.csv
dbt/mercaso_dbt/seeds/master/seed_items.csv
```

The policy contains only natural-language aliases, for example:

```text
美国 / US / USA   → US
西部 / West       → West
可口可乐          → Coca-Cola
饮料              → Beverage
```

The planner validates each alias target against the actual seed canonical value. An alias cannot introduce a value that is absent from its configured value source.

This is intentionally a demo/static value contract. A later runtime phase can replace the value source with governed DataHub / MetricFlow dimension-value discovery without changing the public Tool contract.

## Compatibility authority

The static policy answers only:

```text
Is this dimension/value allowed to become a filter candidate?
```

It does **not** claim that every allowed dimension is reachable from every metric set.

MetricFlow remains the semantic compatibility authority:

```text
query plan
→ mf query ... --explain --show-dataflow-plan
→ only if PASS
→ mf query ... --csv result.csv
```

This preserves the Semantic Layer as the owner of entity paths, join rules and fanout/chasm protection.

## Safety limits

`agent/contracts/semantic_query_policy.yml` currently locks:

```text
max metrics        3
max filters        2
filter operator    EQ only
max group-bys      2
max rows           50
max time range     366 days
explicit time      required
arbitrary SQL      forbidden
raw where          forbidden
explain first      required
```

An explicit filter request whose value cannot be resolved to a governed canonical value returns `CLARIFICATION_REQUIRED`; it does not silently execute an unfiltered query.

## Runtime evidence

Static / phone-only development returns:

```text
status   = DEFERRED
evidence = STATIC_CONTRACT
rows     = []
```

A numeric result becomes an Agent `QUERY_RESULT` claim only after:

```text
PHASE5B_ALLOW_METRICFLOW_QUERY=true
+ real MetricFlow executable
+ explain PASS
+ query PASS
+ requested metric columns present in CSV result
```

Then and only then:

```text
evidence = RUNTIME_VERIFIED
```

## Key files

```text
agent/contracts/semantic_query_policy.yml
agent/contracts/tool_schemas.json
agent/semantic_query/contracts.py
agent/semantic_query/planner.py
agent/semantic_query/executor.py
agent/semantic_query/tool.py
agent/router/deterministic.py
agent/response/composer.py
agent/query_cli.py
agent/generated/semantic_query_samples.json
tests/test_phase5b_semantic_filters_multi_metric.py
infra/runtime/run_phase5b_semantic_query_static.sh
infra/runtime/run_phase5b_metricflow_live.sh
```

## Runtime acceptance still deferred

The following are **not** proved by static closure:

- real Spark / Polaris availability;
- real MetricFlow semantic graph compatibility for the multi-metric + filtered example;
- real generated SQL;
- real query values;
- real dimension values beyond repo-managed demo seed values.

Those remain workstation Runtime Acceptance items.
