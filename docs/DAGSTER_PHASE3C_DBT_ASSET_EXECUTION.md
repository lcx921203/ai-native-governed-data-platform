# Phase 3C — Classified dbt Asset Execution

## 1. Problem

The failure classifier is not useful if the real `@dbt_assets` functions still call
`dbt.cli(...).stream()` directly. The execution path must be:

```text
Dagster dbt Asset
→ DbtCliResource.cli(..., raise_on_error=False)
→ stream normal Dagster/dbt events
→ inspect run_results.json only on failure
→ structured FailureClass
→ run tags + Dagster Failure
→ Retry / Recovery Policy
```

No free-text log parsing is used to grant recovery permission.

## 2. Three dbt execution groups

```text
Deployment / model-change boundary
├─ commerce_dbt_assets
│  └─ seeds / item-store master data / time_spine_daily
└─ commerce_staging_dbt_assets
   └─ global SQL Views; no daily partition ownership

Daily partition boundary
└─ commerce_windowed_dbt_assets
   └─ tag:shopify_windowed
      + SHOPIFY_DAILY_PARTITIONS
      + shopify_effective_start/end vars
```

A Staging View is not rebuilt for every daily partition. It is a global SQL definition;
daily partition responsibility starts again at the partition-aware dbt models.

## 3. One time contract

```text
Dagster logical partition
2026-08-05 00:00 → 2026-08-06 00:00

Effective source read
2026-08-04 23:55 → 2026-08-06 00:00

Same effective window reaches dbt as:
shopify_effective_start / shopify_effective_end
```

The five-minute lookback changes source reading, not the Dagster partition identity.

## 4. Failure ownership

```text
dbt parse non-zero
→ deterministic_code

dbt compile non-zero without stronger structured proof
→ unknown

dbt run_results test.* status=fail
→ data_contract

test/model status=error or non-zero without stronger artifact evidence
→ unknown
```

`unknown` receives neither Step Retry nor automatic cross-run replay. Retry Permission
and Recovery Permission remain separate contracts, but both require positive evidence
that the failure class is safe for that action.

## 5. Current evidence boundary

Implemented / statically checked:
- actual three `@dbt_assets` functions call the classified adapter;
- windowed dbt execution receives the shared Phase 3B effective window;
- Raw / Structured Source / windowed dbt share one Dagster daily partition definition;
- global Staging Views are kept outside the daily partition job;
- recovery consumes one canonical FailureClass contract.

Deferred until a real runtime exists:
- `dagster definitions validate`;
- dbt manifest loading from the restored full project;
- real `run_results.json` failure classification inside a Dagster Run;
- visible structured Run tags in Dagster UI;
- failure → recovery sensor → successful exact-partition materialization.
