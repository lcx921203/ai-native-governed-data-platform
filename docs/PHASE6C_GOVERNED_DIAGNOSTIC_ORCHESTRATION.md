# Phase 6C — Governed Diagnostic Orchestrator + Evidence Response

## 1. Goal

Phase 6C turns the separate Phase 6A / 6B engines into one governed diagnostic chain for a
question such as:

```text
为什么 2026-08-05 Gross Sales 跌了这么多？
```

The orchestrator does **not** give the LLM direct access to MetricFlow, Dagster, DataHub or SQL.
It produces a governed diagnostic result first, then projects only approved evidence into the
existing Phase 4F `ResponseEnvelope / Claim Ledger` boundary.

```text
Natural-language diagnostic question
        ↓
GovernedDiagnosticPlanner
        ↓
One governed Metric + explicit UTC time window
        ↓
Phase 6C runtime gate
        ↓
Dagster exact-partition operational-health snapshot
        ↓
Phase 6A anomaly detection
        ↓
NO ANOMALY ----------------------→ stop
        ↓
WARNING / CRITICAL
        ↓
Operational Health Gate
   ┌───────────────┬──────────────────┬─────────────────┐
   │ HEALTHY       │ UNHEALTHY        │ UNKNOWN/DEFERRED│
   ↓               ↓                  ↓
Business signal    Pipeline suspected Unresolved
suspected          stop 6B            stop 6B
   ↓
Phase 6B Driver Attribution
   ↓
Region / Brand / Category independent lenses
   ↓
DiagnosticEvidenceComposer
   ↓
Claim Ledger
   ↓
Constrained renderer / LLM boundary
```

## 2. Why Operational Health is based on current partition truth

Phase 6C deliberately reuses the Phase 3C principle:

```text
Latest Run SUCCESS / FAILURE
≠
Current exact-partition completeness
```

`DagsterPartitionCompletenessHealthProvider` reads the same Phase 3C exact-partition state used by
recovery. For every daily partition covered by the semantic query window:

- all required consumer marts materialized -> `HEALTHY`;
- any overdue partition incomplete -> `UNHEALTHY`;
- incomplete but freshness deadline not passed -> `UNKNOWN`.

The provider is implemented in:

```text
agent/diagnostic/operational_health.py
```

It lazily imports Dagster so the static/mobile development environment remains importable when
Dagster is not installed. Missing Dagster runtime produces `UNKNOWN / DEFERRED`, never a fabricated
health fact.

## 3. Natural-language planning remains bounded

`GovernedDiagnosticPlanner` resolves exactly one governed metric from the existing Phase 5 routing
vocabulary, then reuses `GovernedSemanticQueryPlanner` to produce the semantic query specification.

Phase 6C does not own metric formulas or SQL.

The first version accepts explicit calendar dates and also resolves only these bounded relative terms:

```text
今天 / 今日 / today      -> concrete UTC date
昨天 / yesterday         -> concrete UTC date
```

The resolved concrete date is passed into the Phase 5 planner. The contract timezone is UTC because
Dagster partitions and the current demo semantic-query time contract are UTC-based.

Multiple metrics are not auto-diagnosed in one request; the user must select one metric.

## 4. Orchestration state machine

The main implementation is:

```text
agent/diagnostic/orchestrator.py
```

### 4.1 No anomaly

If Phase 6A returns `NORMAL`:

```text
DiagnosticStatus = NORMAL
Phase 6B calls = 0
```

### 4.2 Pipeline suspected

If the metric is anomalous and exact-partition operational health is `UNHEALTHY`:

```text
SignalCauseClass = DATA_PIPELINE_SUSPECTED
DiagnosticStatus = DATA_PIPELINE_SUSPECTED
Phase 6B calls = 0
```

This prevents the Agent from claiming that Region / Brand / Category caused a change when the
underlying partition is incomplete.

### 4.3 Operational health unresolved

A runtime-verified metric anomaly can exist while Dagster runtime evidence is unavailable.
Phase 6C preserves the observed anomaly, but does not promote it into a business-driver claim:

```text
Anomaly evidence = RUNTIME_VERIFIED
Operational health = UNKNOWN / DEFERRED
Driver attribution = not executed
DiagnosticStatus = UNRESOLVED
```

### 4.4 Healthy business signal

Only this chain can reach Phase 6B:

```text
WARNING / CRITICAL anomaly
+
MetricFlow evidence = RUNTIME_VERIFIED
+
Operational health = HEALTHY / RUNTIME_VERIFIED
+
SignalCauseClass = BUSINESS_SIGNAL_SUSPECTED
```

Then 6B executes the independent driver lenses.

## 5. Evidence projection into Phase 4F

Phase 6C extends the existing claim kinds with:

```text
ANOMALY_OBSERVATION
OPERATIONAL_HEALTH
DIAGNOSTIC_CLASSIFICATION
DRIVER_ATTRIBUTION
```

Example approved claims for a simulated runtime-verified test:

```text
C01 ANOMALY_OBSERVATION
Gross Sales current=50, median baseline=100, relative change=-50%, CRITICAL DOWN.

C02 OPERATIONAL_HEALTH
Exact queried partition is complete; operational health is HEALTHY.

C03 DIAGNOSTIC_CLASSIFICATION
BUSINESS_SIGNAL_SUSPECTED.
This is a governed evidence classification, not confirmed causal root cause.

C04 DRIVER_ATTRIBUTION
Region lens: West is strongest; change=-40; contribution=80%.

C05 DRIVER_ATTRIBUTION
Brand lens: Coca-Cola is strongest; change=-30; contribution=60%.

C06 DRIVER_ATTRIBUTION
Category lens: Beverage is strongest; change=-50; contribution=100%.
```

A mandatory limitation is also preserved:

```text
Region / Brand / Category are overlapping analytical lenses;
contribution percentages must not be added across lenses.
```

## 6. Runtime-evidence validation

`agent/response/validator.py` now enforces an answer-policy rule that previously existed only as a
contract:

```text
runtime_observed = true
        ↓
claim.evidence must be RUNTIME_VERIFIED
```

It also enforces the existing renderer limits:

```text
Envelope <= 20 claims
Renderer cites <= 8 claims
```

Structured JSON output alone is not sufficient evidence acceptance.

## 7. Runtime gates

Phase 6C has its own explicit permission:

```bash
PHASE6C_ALLOW_DIAGNOSTIC=false
```

A full live diagnostic requires all of:

```bash
PHASE6C_ALLOW_DIAGNOSTIC=true
PHASE6B_ALLOW_DRIVER_ATTRIBUTION=true
PHASE6A_ALLOW_ANOMALY_QUERY=true
PHASE5B_ALLOW_METRICFLOW_QUERY=true
```

The default live wrapper therefore refuses:

```bash
./infra/runtime/run_phase6c_diagnostic_live.sh
# REFUSED / exit 2
```

## 8. Static/mobile behavior

With runtime gates closed, the question can still be parsed into a governed plan, including relative
UTC date resolution, but the final response must remain:

```text
DEFERRED

Diagnostic execution is disabled...
Real diagnostic runtime evidence is unavailable;
no anomaly, operational-health fact, or business driver may be inferred from static contracts.
```

Generated example:

```text
agent/generated/diagnostic_samples.json
```

## 9. Engineering files

```text
agent/diagnostic/
├── __init__.py
├── contracts.py
├── planner.py
├── operational_health.py
├── orchestrator.py
└── response.py

agent/contracts/
└── diagnostic_orchestrator_policy.yml

agent/
├── diagnostic_cli.py
└── build_diagnostic_samples.py

tests/
└── test_phase6c_governed_diagnostic_orchestrator.py

infra/runtime/
├── run_phase6c_diagnostic_static.sh
└── run_phase6c_diagnostic_live.sh
```

## 10. Evidence boundary

Phase 6C is currently:

```text
Natural-language diagnostic planning       PASS
6A → health gate → 6B orchestration        PASS
Exact-partition health provider code       PASS
Claim Ledger projection                    PASS
Runtime claim evidence validator           PASS
Fail-closed live gates                      PASS

Real Dagster event-store health read       DEFERRED
Real MetricFlow anomaly values              DEFERRED
Real Region / Brand / Category attribution DEFERRED
Real OpenAI rendering                       DEFERRED
```

Therefore Phase 6C is **Engineering / Static Closure**, not runtime certification.
