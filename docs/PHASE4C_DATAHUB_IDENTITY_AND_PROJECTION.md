# Phase 4C — DataHub Identity Resolution & Governance Projection

## 1. Why this phase exists

Phase 4B knows the desired governance state for a model such as `orders`, but a governance policy must never guess which DataHub Dataset should receive that metadata.

Phase 4C creates a fail-closed bridge:

```text
dbt model name
    -> expected physical Iceberg identity
    -> DataHub existence / exact-search verification
    -> resolved Dataset URN
    -> governance projection
```

A fuzzy result is discovery evidence, not identity proof.

## 2. Canonical physical identity

This project configures the Iceberg ingestion connector with:

```text
platform          = iceberg
platform_instance = commerce_polaris
env               = DEV
```

The dbt profile materializes marts in the `analytics` namespace. The expected Dataset name is therefore:

```text
commerce_polaris.analytics.<model>
```

Example:

```text
model        = orders
dataset name = commerce_polaris.analytics.orders
expected URN = urn:li:dataset:(urn:li:dataPlatform:iceberg,commerce_polaris.analytics.orders,DEV)
```

This is an **expected identity**, not Runtime proof. It becomes RESOLVED only after DataHub confirms the entity exists or an exact scoped search returns exactly one matching identity.

## 3. Resolution states

| State | Meaning | Governance write permission |
|---|---|---|
| `UNVERIFIED_EXPECTED` | deterministic expected identity, DataHub not queried | NO |
| `RESOLVED_EXPECTED` | expected URN exists in DataHub | YES |
| `RESOLVED_SEARCH_EXACT` | one exact scoped search match | YES |
| `NOT_FOUND` | no exact Dataset exists | NO |
| `REJECTED_NON_EXACT` | only fuzzy / wrong identity candidates found | NO |
| `AMBIGUOUS` | multiple exact candidates | NO |

The policy is intentionally fail-closed.

## 4. Runtime resolver

Static / phone-safe:

```bash
python metadata/datahub/tools/identity_resolver.py --mode expected
```

Real DataHub Runtime later:

```bash
python metadata/datahub/tools/identity_resolver.py --mode resolve
```

The Runtime mode first verifies the deterministic expected URN. If it does not exist, it performs a scoped DataHub search using Dataset + Iceberg + `commerce_polaris` + `DEV`, then applies exact identity filtering locally.

## 5. Governance projection

`build_governance_projection.py` combines:

- resolved Dataset URN
- Domain policy
- Owner policy
- discovery Tags
- Glossary Terms
- Structured Properties

No resolved identity means `BLOCKED_IDENTITY_UNRESOLVED`; the projection does not copy the expected URN into the writable field.

```bash
python metadata/datahub/tools/build_governance_projection.py
```

## 6. Mutating DataHub is explicit

`apply_governance_projection.py` defaults to dry-run and refuses any projection containing unresolved identities.

```bash
python metadata/datahub/tools/apply_governance_projection.py
```

Only this form writes:

```bash
python metadata/datahub/tools/apply_governance_projection.py --apply
```

Before each Dataset write, existence is checked again to protect against stale resolution evidence.

Domain, Tags, Terms, and Owners use DataHub GraphQL mutations. Project-owned Structured Properties are patched individually through OpenAPI v3 so unrelated structured-property assignments are not replaced.

## 7. Runtime execution order

When a real workstation is available:

```text
1. Start Polaris / Iceberg + Spark
2. Start DataHub GMS
3. Run Iceberg ingestion
4. Run dbt build + docs generate
5. Run dbt ingestion
6. Ingest / bootstrap governance definitions
7. Resolve Dataset identities
8. Build governance projection
9. Inspect dry-run projection
10. Apply projection explicitly
11. Re-query DataHub and verify resulting metadata
```

Steps 1–11 remain Runtime DEFERRED in the current phone-only stage.

## 8. Evidence boundary

What static closure can prove now:

- deterministic expected identity construction
- exact-match identity policy
- no fuzzy / cross-platform / cross-env automatic binding
- unresolved Dataset blocks governance writes
- governance projection shape
- source code syntax and contract tests

What it cannot prove without DataHub Runtime:

- Iceberg ingestion actually created the expected URNs
- dbt and Iceberg metadata converged on one physical Dataset identity
- GraphQL governance mutations succeeded
- Structured Property PATCH succeeded
- final DataHub UI / graph state matches the projection

## 9. Governance entity bootstrap

A resolved Dataset URN is not enough to write governance metadata. Every referenced
Domain, CorpGroup, Tag, Glossary Term, and Structured Property must also exist.

Phase 4C therefore adds a separate bootstrap boundary:

```text
Git governance definitions
    -> deterministic governance URNs
    -> governance_bootstrap_plan.json
    -> explicit Runtime bootstrap
    -> exact Dataset identity resolution
    -> governance projection
```

`bootstrap_governance_entities.py` defaults to **PLAN ONLY**. It creates no network
side effects unless `--apply` is supplied.

```bash
python metadata/datahub/tools/bootstrap_governance_entities.py
# no mutation

python metadata/datahub/tools/bootstrap_governance_entities.py --apply
# Runtime only
```

The project uses deterministic URNs so policy files and DataHub do not drift into two
identities:

```text
commerce-order-sales
-> urn:li:domain:commerce-order-sales

data-platform
-> urn:li:corpGroup:data-platform

layer-mart
-> urn:li:tag:layer-mart

commerce.governance.agentReadiness
-> urn:li:structuredProperty:commerce.governance.agentReadiness
```

Glossary remains a separate ingestion concern because `glossary.yml` already acts as its
Git-managed source of truth.

## 10. Runtime write path is hard-gated

The end-to-end Runtime wrapper is:

```bash
PHASE4C_ALLOW_DATAHUB_WRITE=true \
  ./infra/runtime/run_phase4c_datahub_runtime.sh
```

It performs, in order:

```text
Iceberg ingestion
-> dbt ingestion
-> Glossary ingestion
-> governance entity bootstrap
-> exact Dataset identity resolution
-> governance projection build
-> projection dry-run
-> explicit governance application
```

The script refuses to start when `PHASE4C_ALLOW_DATAHUB_WRITE` is not exactly `true`.
This is deliberate: static engineering completion must never silently become Runtime
mutation.

## 11. Phase 4C closure state

Engineering/static closure now covers:

- deterministic physical Dataset expectation
- exact-only Runtime resolver
- deterministic Domain / Group / Tag / Structured Property identities
- governance bootstrap plan
- unresolved-identity write blocking
- projection shape and ownership/tag/term/property references
- explicit Runtime mutation gate
- Python / shell / YAML syntax contracts

Still DEFERRED:

- real DataHub GMS connection
- real Iceberg/dbt/glossary ingestion
- real governance entity bootstrap
- real Dataset identity resolution
- real Domain / Owner / Tag / Term / Structured Property mutation
- re-query proof of final DataHub graph state
