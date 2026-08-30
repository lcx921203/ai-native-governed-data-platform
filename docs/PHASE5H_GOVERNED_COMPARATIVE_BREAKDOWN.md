# Phase 5H — Governed Comparative Breakdown & Contribution Analysis

## 1. Goal

Phase 5G can compare one aggregate period with another. Phase 5H keeps one governed
business dimension in the comparison so the Agent can answer questions such as:

```text
2026-08-01 ~ 2026-08-05 按地区看 Gross Sales
→ 同比呢？
→ 哪个地区增长最多？
→ 总增长主要是谁贡献的？
```

The Agent still does not generate SQL. Both windows are executed through the existing
MetricFlow `explain -> query` path.

## 2. Control flow

```text
Frozen AnalysisSessionState
  metric(s) + time + filters + one business group-by
                ↓
TimeComparisonContext
  PREVIOUS_PERIOD / YEAR_OVER_YEAR
                ↓
ComparativeBreakdownPlan
  current window grouped by the same dimension
  comparison window grouped by the same dimension
                ↓
MetricFlow Explain -> Query (current)
MetricFlow Explain -> Query (comparison)
                ↓
RUNTIME_VERIFIED grouped rows
                ↓
outer join by canonical dimension value
                ↓
current / comparison / absolute change / growth rate
                ↓
optional ranking or contribution
```

## 3. Why the temporal grain is removed

If the session originally contains both `metric_time__day` and `store__region`, Phase 5H
keeps `store__region` but removes the time grain when calculating period-over-period
change. The five-day AOV, for example, must be recomputed by MetricFlow for the full
window; daily AOV values must never be summed.

## 4. Contribution is stricter than comparison

A grouped comparison is valid for ratios and averages, but a contribution percentage is
not. Contribution is allowed only when the canonical MetricFlow definition is safely
additive:

- `simple + agg: sum` -> additive
- `derived` -> additive only when all inputs are additive and the expression is a linear
  `+ / -` expression
- `ratio`, `average`, `count_distinct`, unknown -> not contribution-additive

This classification is derived from dbt / MetricFlow definitions. The contribution engine
does not maintain a second formula registry.

## 5. Aggregate reconciliation

Contribution uses four semantic queries:

```text
current grouped
comparison grouped
current aggregate
comparison aggregate
```

The engine verifies:

```text
sum(group current - group comparison)
≈
aggregate current - aggregate comparison
```

Only after reconciliation may it calculate:

```text
member contribution % = member absolute change / aggregate absolute change * 100
```

This prevents truncated or incomplete grouped rows from creating a plausible but false
contribution story.

## 6. New and disappeared members

For additive metrics, a member missing from one grouped window may safely use zero for
that missing period. This allows explicit handling of new/lost groups:

```text
New region: current=20, previous=0, change=+20
Old region: current=0, previous=10, change=-10
```

For non-additive metrics a missing group is not converted to zero; its change remains
undefined.

## 7. Session behavior

A grouped session now routes comparison follow-ups to Phase 5H rather than silently
collapsing the dimension:

```text
按地区看 Gross Sales
→ 同比呢？              SET_COMPARISON + breakdown plan
→ 哪个地区增长最多？    RANK_BREAKDOWN
→ 谁贡献最大？          CONTRIBUTION_ANALYSIS
```

The original natural-language query is not reparsed. Metric, time, filters, breakdown
dimension and comparison mode come from the structured session state.

## 8. Runtime evidence boundary

Static closure proves planning, state transitions, additivity rules, reconciliation logic
and fail-closed behavior. It does **not** prove real grouped MetricFlow results.

Real evidence still requires:

```text
Spark + Polaris + MetricFlow Runtime
PHASE5B_ALLOW_METRICFLOW_QUERY=true
PHASE5F_ALLOW_SESSION_EXECUTION=true
PHASE5H_ALLOW_BREAKDOWN_QUERY=true
```

Until then, generated samples are `STATIC_CONTRACT` / `DEFERRED` only.
