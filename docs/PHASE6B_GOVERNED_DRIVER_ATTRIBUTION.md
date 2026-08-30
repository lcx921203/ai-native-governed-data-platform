# Phase 6B — Governed Driver Attribution（受治理的驱动因素归因）

## Goal

Phase 6A answers two prerequisite questions:

```text
Is the governed metric anomalous?
Is the data pipeline healthy enough to treat the anomaly as a business signal?
```

Phase 6B starts only after Phase 6A returns a `WARNING` / `CRITICAL`,
`RUNTIME_VERIFIED`, `BUSINESS_SIGNAL_SUSPECTED` result with verified healthy operational
state. It then asks:

```text
Within each governed analytical lens, which members moved most strongly in the anomaly direction?
```

The first bounded driver lenses are:

```text
store__region
item__brand
item__category
```

They are candidates selected by Phase 6A policy. Every actual grouped query still passes
through the existing MetricFlow `Explain -> Query` executor.

## Responsibility chain

```text
Phase 6A RUNTIME_VERIFIED anomaly
  + healthy Dagster/operational evidence
  + exact real median-reference window
        ↓
Phase 6B DriverAttributionPlan
        ↓
for each candidate dimension
  current window GROUP BY dimension
  reference window GROUP BY dimension
        ↓
MetricFlow Explain -> Query
        ↓
reconcile additive metrics to Phase 6A aggregate truth
        ↓
anomaly-direction-aware ranking
        ↓
strongest driver PER LENS
```

There is no LLM-generated SQL path.

## Why the Phase 6A reference window is reused exactly

The Phase 6A baseline is the median of seven real equal-length historical windows. The
median value is mapped back to the first real observed window carrying that value. Phase 6B
uses that exact `reference_spec`.

It does **not** manufacture a synthetic "median day" and invent a Region / Brand / Category
breakdown for it.

## Independent analytical lenses

Region, Brand and Category overlap. A sale can simultaneously be:

```text
Region = West
Brand = Coca-Cola
Category = Beverage
```

Therefore these are separate explanations of the same aggregate movement.

Allowed:

```text
Region lens: strongest = West
Brand lens: strongest = Coca-Cola
Category lens: strongest = Beverage
```

Forbidden:

```text
80% Region contribution
+ 60% Brand contribution
+ 100% Category contribution
= 240% total cause   # invalid
```

Phase 6B intentionally exposes no cross-lens combined contribution percentage.

## Additive metrics and reconciliation

Contribution percentages are allowed only when additivity can be conservatively derived
from the canonical MetricFlow definitions through the existing
`MetricContributionSemantics` logic.

For an additive metric such as `gross_sales`, each lens must reconcile:

```text
SUM(group current)   == Phase 6A current aggregate
SUM(group reference) == Phase 6A median-reference aggregate
```

within the governed tolerance before contribution is calculated.

Then:

```text
member_change = member_current - member_reference
aggregate_change = phase6a_current - phase6a_reference
contribution_percent = member_change / aggregate_change * 100
```

A contribution greater than 100% is valid when other members offset part of that movement.

## Non-additive metrics

For ratio / average style metrics such as `average_order_value`:

- current/reference member values may be compared;
- direction-aware ranking may be produced;
- contribution percentage is not produced;
- a missing member on one side is **not** converted to zero.

This avoids invalid statements such as summing regional AOV changes into total AOV change.

## Direction-aware ranking

The anomaly direction controls what "strongest driver" means:

```text
DOWN anomaly
→ most negative absolute change ranks first

UP anomaly
→ most positive absolute change ranks first
```

This is deliberately different from a generic "largest increase" ranking.

## Partial results

Each dimension lens is independently validated through MetricFlow. If Region and Category
succeed but Brand fails Explain / runtime validation:

```text
status = PARTIAL
Region   = RUNTIME_VERIFIED
Brand    = failed and preserved
Category = RUNTIME_VERIFIED
```

The failed lens is not silently removed, and the successful lenses are not discarded.

If no lens completes, the overall result is `ERROR`.

## Member-limit guard

Each driver lens is capped at 50 members. Returning exactly the configured limit is treated
as potentially truncated and fails closed. Ranking / contribution requires complete member
coverage for the governed lens.

## Runtime gate

Live driver attribution requires all three permissions:

```bash
PHASE6B_ALLOW_DRIVER_ATTRIBUTION=true
PHASE6A_ALLOW_ANOMALY_QUERY=true
PHASE5B_ALLOW_METRICFLOW_QUERY=true
```

The first authorizes attribution, the second proves the upstream anomaly workflow is an
explicit runtime action, and the third authorizes the underlying MetricFlow queries.

## State-drift hardening discovered before 6B

Before Phase 6B was added, the current filesystem was rechecked and stale versions of Phase
5 core contracts/sources were found to have been rematerialized. The problem was not hidden.
The working tree was repaired and the most drift-sensitive files are now locked behind:

```text
infra/contracts/phase5/
├── semantic_query_policy.yml
├── tool_schemas.json
└── canonical_sources/
    ├── semantic_query_planner.py
    ├── semantic_query_executor.py
    └── analysis_session.py

infra/runtime/sync_phase5_contracts.py --repair
```

`run_phase5_static_closure.sh`, Phase 6 static runners, and Phase 6 closure materialize this
canonical baseline before testing. This protects the operational implementation from stale worktree rematerialization regardless of the external mechanism that caused it.

## Key files

```text
agent/driver_attribution/contracts.py
agent/driver_attribution/attribution.py
agent/contracts/driver_attribution_policy.yml
tests/test_phase6b_governed_driver_attribution.py
infra/runtime/run_phase6b_driver_attribution_static.sh
infra/runtime/run_phase6b_driver_attribution_live.sh
agent/contracts/phase6_capability_manifest.yml
infra/runtime/run_phase6_static_closure.sh
```

## Evidence boundary

Static closure proves:

- strict Phase 6A prerequisites;
- exact median-reference-window reuse;
- independent driver lenses;
- additivity-derived contribution permission;
- reconciliation guards;
- anomaly-direction-aware ranking;
- partial-lens behavior;
- member-limit fail-closed behavior;
- runtime gates default closed.

It does **not** prove real Region / Brand / Category driver values until a workstation runs
MetricFlow/Spark/Polaris and real operational health is available. Runtime evidence remains
`DEFERRED`.


## Current acceptance result

```text
Phase 6B focused static runner: 43 / 43 PASS
Whole repository regression: 236 / 236 PASS
Phase 5 static closure: PASS
Phase 6 static closure: PASS
Live Phase 6B gate: REFUSED / exit 2 by default
```
