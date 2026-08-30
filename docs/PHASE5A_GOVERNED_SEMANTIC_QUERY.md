# Phase 5A · Governed Semantic Query Tool（受治理的语义查询工具）

## 1. Goal

Phase 4 built a governed **metadata Agent**: it can explain metrics, entities, ownership,
lineage, automation contracts, and evidence boundaries. Phase 5 starts **business-value
querying**.

Phase 5A deliberately does **not** implement Text-to-SQL. The new path is:

```text
Natural-language question
    -> deterministic METRIC_QUERY intent
    -> exact governed metric id
    -> bounded Semantic Query Plan
    -> MetricFlow explain / dataflow validation
    -> MetricFlow query
    -> CSV result
    -> RUNTIME_VERIFIED query-result claim
    -> constrained answer renderer
```

MetricFlow remains the calculation authority. The Agent never reconstructs the metric SQL.

## 2. Why MetricFlow instead of LLM-generated SQL

The canonical metric definitions already live in dbt / MetricFlow. For example:

```text
activity_net_sales
= sales_before_reversal - sales_reversal_amount
```

That formula is defined in `dbt/mercaso_dbt/models/metrics/sales.yml`. Phase 5A sends the
metric **name** to MetricFlow; it does not copy the formula into an Agent query builder.

This gives a strong boundary:

```text
LLM / Router decides what the user asked for
MetricFlow decides how the governed metric is computed
Spark / Iceberg execute the resulting semantic query
```

## 3. Current Phase 5A query contract

The first version is intentionally small.

Allowed:

- exactly one metric;
- metric must exist in `metadata/datahub/governance/metric_registry.yml`;
- one explicit date or one explicit date range;
- optional maximum two `group_by` dimensions;
- maximum 50 returned rows;
- MetricFlow `--explain --show-dataflow-plan` must pass before the data query;
- runtime execution requires an explicit environment gate.

Not allowed yet:

- arbitrary SQL;
- free-form `where` clauses;
- LLM-generated predicates;
- implicit full-history queries;
- more than one metric per query;
- ranges longer than 366 calendar days.

These are not limitations of MetricFlow itself. They are Phase 5A Agent safety limits.

## 4. Time contract

MetricFlow's open-source CLI accepts `--start-time` and `--end-time`. The documented end
boundary is inclusive, so a single-day query is normalized to the full UTC calendar day:

```text
2026-08-05
-> start = 2026-08-05T00:00:00Z
-> end   = 2026-08-05T23:59:59Z
```

Phase 5A requires an explicit calendar date. This prevents a short question such as
`activity_net_sales 是多少？` from silently scanning or aggregating all history.

That question returns `CLARIFICATION_REQUIRED` instead of a number.

## 5. Group-by language mapping

`agent/contracts/semantic_query_policy.yml` contains only **natural-language aliases** for
query dimensions. It does not own the dimension semantics.

Examples:

```text
按天       -> metric_time__day
按周       -> metric_time__week
按地区     -> store__region
按商品类别 -> item__category
```

The actual dimension/entity definitions still live in dbt Semantic YAML.

A non-trivial join path is never trusted solely because the alias exists. Before querying,
Phase 5A runs MetricFlow `--explain --show-dataflow-plan`. This is important because a
syntactically plausible dimension can still be semantically unsafe. The existing project
acceptance case `order_count by item__category` is an example that MetricFlow must reject
because of unsafe fan-out semantics.

## 6. Command shape

For local/open-source MetricFlow the command is constructed as an argument vector, never a
shell string:

```text
mf query
  --metrics activity_net_sales
  --group-by metric_time__day
  --start-time 2026-08-01T00:00:00Z
  --end-time 2026-08-05T23:59:59Z
  --limit 20
```

Before the actual query, the same governed request is validated as:

```text
mf query ... --explain --show-dataflow-plan
```

Only after that command succeeds may the executor add:

```text
--csv <temporary-result-path>
```

The Agent never exposes a `sql` parameter or a raw `where` parameter.

## 7. Static vs Runtime evidence

On the current phone/static environment:

```text
Question
-> METRIC_QUERY
-> Semantic Query Plan
-> status = DEFERRED
-> rows = []
-> evidence = STATIC_CONTRACT
```

The response envelope may state the planned metric, time range, group-by, and limit, but it
must also state that no numeric result was observed.

Only a real successful MetricFlow query produces:

```text
status = COMPLETE
evidence = RUNTIME_VERIFIED
```

and only then does Phase 4F create a `QUERY_RESULT` claim marked `runtime_observed=true`.

## 8. Runtime safety gate

Live querying requires:

```bash
PHASE5A_ALLOW_METRICFLOW_QUERY=true
```

and an existing MetricFlow CLI, defaulting to:

```text
.venv-mf/bin/mf
```

The compatibility semantic spec must also already exist. The query tool does not install
packages or bootstrap the data platform itself.

Run the existing MetricFlow runtime validation first on a workstation:

```bash
bash infra/runtime/run_metricflow_validation.sh
```

Then the explicit live acceptance wrapper is:

```bash
PHASE5A_ALLOW_METRICFLOW_QUERY=true \
bash infra/runtime/run_phase5a_metricflow_live.sh
```

## 9. Examples

### Exact day

```text
2026-08-05 activity_net_sales 是多少？
```

Plan:

```text
metric = activity_net_sales
time   = 2026-08-05 UTC
group_by = none
```

### Daily trend over an explicit range

```text
2026年8月1日到2026年8月5日，按天看 activity_net_sales 是多少？
```

Plan:

```text
metric = activity_net_sales
group_by = metric_time__day
```

### Business dimension

```text
2026-08-01 到 2026-08-05 按地区看 gross_sales 是多少？
```

Plan:

```text
metric = gross_sales
group_by = store__region
```

The business-dimension join is still validated by MetricFlow explain at runtime.

## 10. Engineering files

```text
agent/
├── semantic_query/
│   ├── contracts.py
│   ├── planner.py
│   ├── executor.py
│   └── tool.py
├── contracts/
│   └── semantic_query_policy.yml
├── query_cli.py
├── build_semantic_query_samples.py
└── generated/
    └── semantic_query_samples.json

tests/
└── test_phase5a_semantic_query.py

infra/runtime/
├── run_phase5a_semantic_query_static.sh
└── run_phase5a_metricflow_live.sh
```

## 11. Evidence boundary

Phase 5A engineering/static closure proves:

- governed metric allowlist;
- explicit bounded time requirement;
- row/range/group-by bounds;
- no SQL / free-form where surface;
- MetricFlow explain-before-query ordering;
- deterministic Router integration;
- no numeric result on a static-only environment;
- query-result claims require `RUNTIME_VERIFIED` evidence.

It does **not** prove a real Spark/Polaris/MetricFlow result until the workstation runtime is
started and the live acceptance wrapper succeeds.
