# Phase 6A — Governed Anomaly Detection & Driver Planning

## 1. Goal

Phase 5 closed the governed query / comparison / breakdown chain. Phase 6A adds the first
diagnostic capability: decide whether one governed metric is anomalous, without letting an LLM
invent a baseline or silently attribute a data-pipeline problem to the business.

The first version is intentionally small and auditable:

```text
one governed metric
    ↓
current aggregate window
    ↓
7 immediately preceding equal-length windows
    ↓
median baseline
    ↓
relative deviation
    ↓
NORMAL / WARNING / CRITICAL
    ↓
operational-health evidence gate
    ↓
BUSINESS_SIGNAL_SUSPECTED / DATA_PIPELINE_SUSPECTED / UNRESOLVED
    ↓
only then create a bounded driver-analysis plan
```

## 2. Why a median baseline

The detector uses the median of seven observed previous equal-length periods instead of a mean.
For a demo platform this gives a deterministic, explainable baseline that is less sensitive to one
large historical spike. The baseline is still an operational policy, not a MetricFlow metric formula.

The seven-period count is odd so the median maps to a real observed period. That real period becomes
the reference window for later driver breakdown, avoiding a synthetic baseline that cannot be
reconciled by dimension.

## 3. Threshold policy

`agent/contracts/anomaly_detection_policy.yml` currently uses demo operational thresholds:

```text
|relative deviation| < 20%  -> NORMAL
20% .. <35%                -> WARNING
>=35%                      -> CRITICAL
```

These thresholds are not copied into dbt / MetricFlow. They are anomaly-operating policy and can
later be calibrated per metric from real production history.

## 4. Evidence boundary

An anomaly claim is allowed only when current and all baseline MetricFlow queries are:

```text
status   = COMPLETE
evidence = RUNTIME_VERIFIED
```

Static contracts can build the plan, but cannot certify an anomaly.

A business-driver attribution needs an additional operational-health snapshot:

```text
MetricFlow anomaly evidence RUNTIME_VERIFIED
+
Dagster / operational health RUNTIME_VERIFIED + HEALTHY
=
BUSINESS_SIGNAL_SUSPECTED
```

If runtime health is unhealthy, the classification is `DATA_PIPELINE_SUSPECTED` and business-driver
analysis is blocked. If runtime health is unavailable, cause classification remains `UNRESOLVED`.

## 5. Driver planning only in 6A

6A does not yet execute driver attribution. It creates at most three governed dimension candidates:

```text
store__region
item__brand
item__category
```

Each candidate must still pass MetricFlow Explain before any later breakdown query. Phase 6B can
execute and rank these driver analyses on top of Phase 5H.

## 6. Runtime gates

```bash
PHASE6A_ALLOW_ANOMALY_QUERY=false
PHASE5B_ALLOW_METRICFLOW_QUERY=false
```

Both must be explicitly enabled for live anomaly execution.

## 7. Static acceptance

```bash
./infra/runtime/run_phase6a_anomaly_static.sh
```

Real Spark / Polaris / MetricFlow and Dagster operational-health evidence remains **DEFERRED**.
