# Phase 5G — Governed Time Context & Comparative Analysis

## Goal

Phase 5G adds a structured comparison context to the governed analysis session. A follow-up such as `和前5天比呢？`, `环比呢？`, `同比呢？`, or `增长了多少？` is treated as a bounded delta over the frozen semantic-query state. The original question is not reparsed.

The phase does **not** introduce a second SQL path. Both the current and comparison windows are represented as `SemanticQuerySpec` objects and must still pass the existing MetricFlow `explain -> query` runtime path.

## Responsibility chain

```text
AnalysisSessionState
  metric / time / grain / filters
        ↓
TimeComparisonContext
  PREVIOUS_PERIOD | YEAR_OVER_YEAR
        ↓
ComparativeQueryPlan
  current aggregate window
  comparison aggregate window
        ↓
MetricFlow explain/query for BOTH windows
        ↓
RUNTIME_VERIFIED + RUNTIME_VERIFIED
        ↓
absolute_change / growth_rate_percent
```

## Why comparison is a separate context

The session keeps the original semantic-query state unchanged. For example:

```text
2026-08-01 ~ 2026-08-05
metric = gross_sales
filter = store__region = West
group_by = metric_time__day
```

After `和前5天比呢？`, the state still keeps the daily display grain. The comparison context is added separately:

```text
comparison.mode = PREVIOUS_PERIOD
comparison.requested_days = 5
```

The comparative summary intentionally removes only temporal `metric_time__*` group-by for growth math and compares the two whole windows:

```text
current     2026-08-01 ~ 2026-08-05
comparison  2026-07-27 ~ 2026-07-31
```

This prevents invalid operations such as summing daily AOV values to calculate period AOV growth.

## Supported comparison modes

### Previous equal period

`环比`, `和前一期比`, or `和前5天比` uses the immediately preceding equal-length window.

For a five-day current window:

```text
current     Aug 01 ~ Aug 05
previous    Jul 27 ~ Jul 31
```

If the user explicitly asks for `前7天` while the current window is five days, Phase 5G returns `CLARIFICATION_REQUIRED`. It does not compare unequal windows silently.

### Year over year

`同比` and `去年同期` shift the same calendar window back one year:

```text
current     2026-08-01 ~ 2026-08-05
comparison  2025-08-01 ~ 2025-08-05
```

For a Feb-29 boundary, the non-leap comparison year clamps that boundary to Feb-28.

## Group-by boundary

Phase 5G v1 supports aggregate period comparison. Temporal group-by (`metric_time__day/week/month`) is removed only from the derived comparison query while the session state remains unchanged.

Non-time group-by such as `store__region` is **not** silently collapsed. It returns `CLARIFICATION_REQUIRED`; per-dimension aligned comparison is intentionally deferred to a later phase.

## Derived values

For every governed metric, runtime comparison returns:

```text
current_value
comparison_value
absolute_change = current - comparison
growth_rate_percent = (current - comparison) / comparison * 100
```

If the comparison value is zero, `growth_rate_percent` is `null` with an explicit warning. The system never invents infinity or an arbitrary percentage.

## Evidence boundary

A comparison plan can be built statically, but derived numeric change requires **both** window queries to be `RUNTIME_VERIFIED`.

```text
STATIC_CONTRACT
  → may describe windows and metric/filter context
  → may NOT claim growth

RUNTIME_VERIFIED current
+ RUNTIME_VERIFIED comparison
  → may derive absolute change and growth rate
```

## Runtime gates

Live comparison through an analysis session requires all three explicit gates:

```bash
PHASE5G_ALLOW_COMPARATIVE_QUERY=true
PHASE5F_ALLOW_SESSION_EXECUTION=true
PHASE5B_ALLOW_METRICFLOW_QUERY=true
```

Without them, runtime execution is refused or deferred.

## Static acceptance

```bash
./infra/runtime/run_phase5g_time_comparison_static.sh
```

This proves contracts, state transitions, window derivation, derived-math guards, and regressions. It does **not** prove a real Spark / Polaris / MetricFlow comparison.
