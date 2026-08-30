# Phase 5 Closure Audit — Governed Data Agent Query / Analysis Chain

## 1. Purpose

Phase 5A–5H has grown from a bounded MetricFlow query into a multi-turn analytical Agent
surface. Before adding another feature phase, this audit checks whether the repository itself
still proves the architecture it claims.

The audit is intentionally **source-first**:

```text
Code + contracts
    -> tests
    -> reproducible generated samples
    -> static closure

Generated JSON / old documentation
    != source of truth
```

Real DataHub, Dagster, MetricFlow, Spark, Polaris, and OpenAI runtime evidence remains
**DEFERRED**.

---

## 2. Scope

The closure covers the whole governed analysis chain:

```text
5A  single governed metric query
5B  structured filters + multi-metric query
5C  dimension-value discovery
5D  dimension-value resolution
5E  clarification continuation
5F  analysis-session state
5G  time comparison
5H  comparative breakdown + contribution
```

It also audits the Phase 4D/4E read-tool and router surface because Phase 5 depends on it.

---

## 3. Findings found during the audit

### F01 — P0: public Tool schema had drifted away from executable code

Before this audit, `agent/contracts/tool_schemas.json` declared ten public tools, but the
actual `GovernedMetadataTools` implementation only retained two methods. A green Phase 5
unit suite therefore did **not** prove that the end-to-end Agent tool surface was executable.

Fixed by restoring the bounded read-only implementations for:

```text
search_metadata
get_entity_context
get_metric_context
get_dataset_context
get_lineage_context
get_runtime_context
get_dimension_values
resolve_dimension_value
```

`query_semantic_metric` and `query_semantic_metrics` remain special-cased through the
MetricFlow planner/executor.

### F02 — P0: deterministic Router contract was missing / behavior had regressed

`agent/contracts/intent_routing.yml` was missing while the documentation and generated
routing samples still claimed it existed. The live Python Router had regressed to mostly
Metric recognition.

Fixed by restoring a machine-readable routing contract and the bounded deterministic routes
for:

```text
METRIC_QUERY
METRIC_DEFINITION
ENTITY_CONTEXT
DATASET_GOVERNANCE
LINEAGE_QUERY
RUNTIME_DIAGNOSIS
DIMENSION_VALUE_DISCOVERY
METADATA_DISCOVERY
```

Unknown metric intent still fails closed into governed search and is never auto-bound to a
similarly named Entity.

### F03 — P0: generated metadata evidence referenced missing governance sources

`governance_context.json`, `context_samples.json`, and older response/routing samples still
contained Source Locations such as:

```text
metadata/datahub/governance/glossary.yml
metadata/datahub/governance/entity_registry.yml
metadata/datahub/governance/asset_policy.yml
metadata/datahub/generated/dataset_identity_resolution.json
```

but those source files had disappeared from the working tree.

That meant the generated artifact looked complete while its claimed evidence path was not
reproducible.

Fixed by restoring the Git-owned governance inputs from the already-existing Phase 4B
`governance_context.json` closure artifact, and rebuilding the deterministic expected
Dataset identity artifact as:

```text
status       = UNVERIFIED_EXPECTED
resolved_urn = null
runtime      = not verified
```

No DataHub Runtime success was invented.

### F04 — P1: Phase 5A and 5C had no independent current acceptance files

Their functionality was indirectly exercised by later tests, but the repository no longer
contained their dedicated acceptance modules / runners.

Fixed by adding:

```text
tests/test_phase5a_semantic_query.py
tests/test_phase5c_dimension_value_discovery.py

infra/runtime/run_phase5a_semantic_query_static.sh
infra/runtime/run_phase5c_dimension_values_static.sh
```

and restoring the missing 5A–5E live/static wrappers.

### F05 — P1: generated Agent samples were not fully reproducible

Several generated JSON files existed without the builder scripts that originally produced
them. This made stale samples easy to mistake for current evidence.

Fixed by restoring deterministic builders for:

```text
context
routing
answer
semantic query
dimension values
dimension resolution
clarification
analysis session
time comparison
comparative breakdown
```

The Phase 5 closure runner regenerates them with every live gate forced closed before tests
run.

### F06 — P1: Runtime gates were fragmented and `.env.example` did not list the full chain

Only the latest comparison/breakdown gates were visible in the environment template.

Fixed by documenting all active Agent/semantic runtime gates as `false` by default and
adding a capability manifest that records the gate dependency for each phase.

### F07 — P1: a green unit suite was insufficient to detect architecture drift

The pre-audit repository reported:

```text
183 / 183 PASS
```

but F01–F06 still existed because no test asserted that:

- Tool schemas were executable;
- Router contracts existed;
- generated source locations existed;
- every Phase 5 capability had its policy / implementation / test / static runner / live
  runner;
- all live gates defaulted closed.

Fixed by adding `tests/test_phase5_closure_contract.py` and the capability manifest.

---

## 4. New closure control plane

### Capability manifest

Canonical inventory:

```text
agent/contracts/phase5_capability_manifest.yml
```

For each phase it records:

```text
capability
policy
implementation files
tests
static runner
live runner
runtime gates
runtime evidence state
```

All 5A–5H runtime evidence is currently:

```text
DEFERRED
```

### Static closure entry point

```bash
./infra/runtime/run_phase5_static_closure.sh
```

The runner:

1. forces every live Agent / MetricFlow / OpenAI gate closed;
2. rebuilds generated Agent samples;
3. parses YAML / JSON contracts;
4. compiles Agent Python;
5. validates Phase 5 shell syntax;
6. runs the entire repository test suite;
7. executes every Phase 5 live wrapper and proves it refuses with exit code `2` while gates
   are closed.

---

## 5. Final static evidence

Final closure result in the current working tree:

```text
204 / 204 tests PASS
Python compile PASS
Phase 5 shell syntax PASS
YAML / JSON parse PASS
Generated source-reference integrity PASS
Public Tool schema ↔ executable surface PASS
Router ↔ Tool schema PASS
All Phase 5 live wrappers default REFUSED / exit 2 PASS
```

This is **engineering/static closure**, not production certification.

---

## 6. Runtime evidence still DEFERRED

The following remain deliberately unproven until a workstation/runtime is available:

```text
Real DataHub Dataset reads / lineage
Real Dagster Run / failure / recovery history
Real MetricFlow explain/query against Spark + Polaris
Real dynamic dimension-value universe
Real multi-turn session query execution
Real previous-period / YoY numeric comparison
Real grouped contribution reconciliation on production-like data
Real OpenAI provider call
```

Static tests or Fake Runners must never upgrade those states to `RUNTIME_VERIFIED`.

---

## 7. Phase 5 architecture after closure

```text
User
  ↓
Deterministic Router
  ↓
Governed Tool Surface
  ├── metadata context
  ├── semantic query
  ├── dimension discovery / resolution
  └── runtime context (DEFERRED without real evidence)
  ↓
MetricFlow semantic plan
  ↓
Explain-before-query
  ↓
Evidence boundary
  ↓
Clarification / Analysis Session
  ↓
Time comparison
  ↓
Breakdown / Contribution
  ↓
Governed Response Envelope
  ↓
Constrained Renderer
```

No layer exposes arbitrary SQL or a free-form `where` escape hatch.

---

## 8. Closure decision

Phase 5A–5H is now **STATICALLY CLOSED** on the current working tree.

The next feature should not be started by assuming generated artifacts are correct. New
work must first preserve the capability-manifest and closure-contract invariants introduced
here.

The stable packaged source ZIP is still the earlier consolidated snapshot; this Phase 4/5
working tree has **not** been repackaged yet.
