# Source State — Phase 6 Final Static Closure Snapshot

This working tree is the consolidated engineering source after **Phase 6A–6F Final Static Closure**.
It supersedes the older Phase 3C-only source-state note for this development tree.

## Closure state

```text
Phase 3C automation / recovery contracts      STATIC CLOSED
Phase 4 metadata / governance / agent reads  ENGINEERING COMPLETE, runtime deferred
Phase 5 governed semantic analysis           STATIC CLOSED
Phase 6 diagnostics / incident / approval    STATIC_ENGINEERING_CLOSED
Phase 7 real workstation runtime             NOT STARTED / DEFERRED
```

Phase 6 is intentionally frozen at **6A–6F**. There is no planned Phase 6G feature expansion.
The next engineering phase is **Phase 7 · Real Runtime**.

## Phase 6 authority boundary

```text
dbt + MetricFlow
  -> business metric / semantic calculation authority

Phase 3C Dagster Recovery Policy + Recovery Sensor
  -> automated replay policy and execution authority

Phase 6A–6D
  -> read-only anomaly / diagnosis / incident evidence

Phase 6E
  -> advisory incident-response plan only

Phase 6F
  -> human approval state + approval audit only

Agent
  -> NO production recovery / backfill / SQL write authority
```

`APPROVED` never means `EXECUTED`. Any post-approval external execution must re-read current
incident truth and the Phase 3C recovery policy before acting.

## Evidence boundary

Static tests may use fixtures labelled `RUNTIME_VERIFIED` to exercise contracts. Those fixtures are
**not** production observations. Real evidence remains **DEFERRED** for:

- Dagster daemon / Run Storage / exact-partition runtime acceptance;
- MetricFlow + Spark + Polaris/Iceberg metric queries;
- DataHub runtime identities / lineage;
- live anomaly and driver-attribution values;
- authenticated approval identity and production audit persistence;
- manual backfill / recovery execution after approval;
- OpenAI live diagnostic rendering.

Only real workstation execution may upgrade a concrete observation to `RUNTIME_VERIFIED`.

## Frozen contract lock

`infra/contracts/phase6/phase6_static_closure_lock.json` records SHA-256 hashes for the critical
Phase 6 policies, implementations, response evidence boundary, Phase 3C recovery dependencies, and
the Shopify dbt source contract. `tests/test_phase6_final_closure.py` fails if those frozen contracts
drift without an explicit re-closure.

## Canonical closure

Run:

```bash
./infra/runtime/run_phase6_static_closure.sh
```

The runner repairs the Phase 5 canonical materialization, forces all live gates closed, rebuilds
static Phase 6 examples, parses contracts, compiles Agent Python, validates shell syntax, runs the
whole repository suite, and confirms every Phase 6 live wrapper refuses by default.

See `docs/PHASE6_CLOSURE_AUDIT.md` for the final audit and `PROJECT_STATUS.md` for the current
runtime backlog.

## Milestone package

Phase 6 Final Static Closure is packaged as:

```text
commerce-modern-data-platform-learning-phase6-static-closure.zip
commerce-modern-data-platform-learning-phase6-static-closure.sha256
```

The package excludes caches, Python bytecode, dbt targets/logs and other disposable runtime output.
It includes source, contracts, tests, generated static examples, documentation and runtime acceptance
wrappers.


## Post-closure current-source evolution — Serving Layer + Trino

The historical Phase 6 snapshot above remains historical evidence. The current source tree now also contains a Serving Layer for fixed BI/API consumers: MetricFlow -> fixed Serving Contract -> Dagster -> Spark/Iceberg -> Trino -> BI/FastAPI. This extension preserves the existing authority boundaries: Serving does not own metric formulas, Agent dynamic analytics still uses MetricFlow, and no new production write authority is granted to the Agent. Static repository acceptance after the extension is **391 passed**; real Serving runtime evidence remains unexecuted in this environment.


### Serving governance / consumer lineage extension

DataHub governance now follows the fixed consumer path through the Serving Dataset, Dagster Serving Export, logical BI Dashboard contract, and OpenAPI endpoint metadata. The Serving Dataset remains `REFERENCE_ONLY` for the Agent and explicitly records `Metric Authority = METRICFLOW`. API endpoint lineage is exact-identity only after real OpenAPI ingestion; guessed/fuzzy endpoint URNs are refused. Real DataHub writes remain runtime-gated and are not claimed by static acceptance.


## Final completeness audit — 2026-08-21

The current source now includes the post-Serving completeness hardening: Chinese-first Chapter 09 comment contracts, corrected host-facing DataHub OpenAPI ingestion (`localhost:8081`), deterministic OpenAPI drift validation, explicit Serving Runtime Evidence, Serving Governance `verify-all`, and a final **13/13** Runtime Evidence closure contract. Root `pytest` execution is also normalized through `pytest.ini`. Current source/static acceptance is **403 passed**. No real 13/13 Runtime success is claimed until the workstation acceptance runners produce `.runtime` evidence.


## Post-closure engineering hardening — GitHub CI + dependency locks

The current source now contains GitHub-native CI and a per-runtime dependency-lock policy. CI keeps canonical dbt, MetricFlow compatibility, Dagster, DataHub, RAG, MCP, Serving and Streaming dependency surfaces independently resolvable instead of hiding conflicts in one global environment. Lock generation is fixed to Python 3.11 / Linux x86_64, uv 0.12.1, SHA-256 hashes and the 2026-08-21 publication cutoff. The current offline environment cannot resolve PyPI transitives, so no fake lock contents are claimed; the first complete lock artifact must be generated online and committed. Source/static acceptance is **403 passed**.

## 2026-08-30 current-source evolution — Order Lifecycle Conversion Metrics

An explicit business metric contract has now approved `order_lifecycle_snapshot` for Semantic Layer use. The current source adds a one-row-per-Order Semantic Model plus governed Conversion Metrics for Order → Paid (24h), Order → Fulfillment (3d), and Order → Delivered (7d). The design deliberately reuses the existing accumulating snapshot so Payment / Fulfillment detail facts are reduced to Order Grain before MetricFlow event matching, avoiding direct multi-fact fanout.

The local `dbt-metricflow==0.13.0` compatibility bridge now maps latest-spec Conversion Metrics into legacy `conversion_type_params`, and the compatibility project includes a thin `order_lifecycle_snapshot` view. DataHub governance promotes the lifecycle dataset from `REFERENCE_ONLY` to inherited `SEMANTIC_READY`; no `RUNTIME_VERIFIED` claim is made without real MetricFlow/DataHub runtime evidence.

## 2026-08-30 current-source evolution — Metric Version Lifecycle Governance

The current source now implements an explicit **Metric Version Lifecycle** governance contract on top of the existing dbt + MetricFlow metric authority. `metric_registry.yml` remains the current governed consumer surface and now points each governed Metric to a `current_version`; append-only version history is stored separately in `metric_lifecycle.yml` with lifecycle status, change type, effective-time boundary, superseded version, owners and a SHA-256 canonical definition fingerprint.

The fingerprint gate blocks silent rewrites of the currently ACTIVE metric definition: a semantic formula / aggregation / entity / time / conversion-window change must be treated as an intentional versioned change instead of overwriting the existing business version. Baseline V1 records use `governance_adopted_at=2026-08-30` while leaving unknown historical `effective_from` unset, so the repository does not fabricate original business launch dates.

Static implementation includes the lifecycle validator, CI gate, regression tests and Agent Metric Context exposure. This evolution does **not** claim that historical versions have already been dual-run in real MetricFlow Runtime, nor that Backfill / Forward Fix has been executed. Those remain Runtime-evidence concerns.

Current whole-repository source/static suite after this lifecycle evolution: **413 passed**.
