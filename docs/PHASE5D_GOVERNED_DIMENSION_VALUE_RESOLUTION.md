# Phase 5D · Governed Dimension Value Resolution

## 1. Goal

Phase 5C answers **which values exist** for a governed MetricFlow dimension. Phase 5D answers a different question:

> When a user writes a filter literal in natural language, which canonical governed dimension/value does that literal mean?

The resolver sits before the MetricFlow query planner. It may only create a query predicate when the value is uniquely resolved.

```text
User literal
  ↓
Governed Dimension Value Resolver
  ↓
Phase 5C value universe
  ↓
Exact / normalized / alias matching
  ↓
unique? ── yes ──> Structured EQ filter
  │
  └─ no
      ↓
    fuzzy candidates only
      ↓
    clarification required
```

No raw SQL or caller-supplied MetricFlow `where` expression is accepted.

## 2. Why Phase 5D exists

Phase 5B intentionally used a small static alias vocabulary such as:

```text
美国西部 → West
可口可乐 → Coca-Cola
```

That vocabulary describes **how users may say a known value**. It must not become the authoritative list of business values.

Real values can change. A new brand such as `Pepsi` may appear in the Runtime dimension universe without ever being added to the alias YAML.

Therefore:

```text
Alias vocabulary
= language understanding aid

Dimension value universe
= Seed reference in static mode
  or MetricFlow Runtime in workstation mode
```

Phase 5D resolves against the second source.

## 3. Resolution modes

Only the following unique matches may resolve automatically:

- `CANONICAL_EXACT`
- `NORMALIZED_EXACT`
- `ALIAS_EXACT`

Example:

```text
coca cola
→ normalize
→ Coca-Cola
```

A fuzzy match is **candidate-only**:

```text
Coca Colaa
→ candidate: Coca-Cola
→ CLARIFICATION_REQUIRED
```

Even a high fuzzy score never silently becomes a query filter.

## 4. Ambiguity is fail-closed

Without a dimension hint, Phase 5D may infer the dimension only when the exact value is unique across the governed value universes.

If one literal exists in multiple dimensions:

```text
Shared
→ store__region = Shared
→ item__brand   = Shared
```

then:

```text
CLARIFICATION_REQUIRED
```

The user must identify the intended dimension.

## 5. Runtime value example

Static demo seeds currently do not contain `Pepsi`, so phone/static mode returns:

```text
Pepsi
→ NOT_FOUND
→ filter is NOT dropped
```

When real MetricFlow dimension discovery later returns:

```text
item__brand
├── Coca-Cola
├── Generic
└── Pepsi
```

Phase 5D resolves:

```text
Pepsi
→ item__brand = Pepsi
→ CANONICAL_EXACT
→ RUNTIME_VERIFIED
```

The new Runtime value is **not written back** into `value_aliases`.

## 6. Semantic-query integration

The Phase 5B planner now has a second-stage resolution path.

### Static known value

```text
2026-08-05 美国西部地区 gross_sales 是多少？
```

still resolves through the cheap static canonical/alias path.

### Dynamic value with explicit dimension

```text
2026-08-05 品牌为 Pepsi 的 gross_sales 是多少？
```

becomes:

```text
metric = gross_sales
raw filter = Pepsi
dimension hint = item__brand
    ↓
Phase 5D resolver
    ↓
unique Runtime exact match
    ↓
Structured Filter
item__brand EQ Pepsi
    ↓
MetricFlow Explain
    ↓
MetricFlow Query
```

### Dynamic value without dimension

```text
2026-08-05 只看 Pepsi 的 gross_sales 是多少？
```

can infer `item__brand` only if `Pepsi` is a unique exact value across the governed dimensions.

### Unknown value

```text
2026-08-05 只看 UnknownBrand 的 gross_sales 是多少？
```

must return `CLARIFICATION_REQUIRED`; an unfiltered query is forbidden.

## 7. Important correctness fix discovered during Phase 5D

The existing Phase 5B phrase matcher had a normalized fallback that could allow a short value such as `CA` to match inside `Coca`.

That meant this text was dangerous:

```text
品牌为 coca cola
```

because `CA` could be accidentally detected as `store__state = CA`.

Phase 5D fixed the normalized fallback so it keeps alphanumeric boundaries. Regression coverage now proves that `coca cola` produces only:

```text
item__brand = Coca-Cola
```

and never a spurious state filter.

## 8. Evidence model

| Value source | Resolution evidence | Meaning |
|---|---|---|
| Repo seed fallback | `STATIC_CONTRACT` | Reference-only canonical value |
| MetricFlow Runtime | `RUNTIME_VERIFIED` | Runtime-observed dimension value |
| Fuzzy similarity | same source evidence, but candidate-only | Never auto-applied |

Resolution evidence does not replace query-result evidence. A resolved Runtime value still has to pass MetricFlow Explain and the actual query before a numeric result may become `RUNTIME_VERIFIED`.

## 9. Files

```text
agent/
├── dimension_resolution/
│   ├── contracts.py
│   ├── resolver.py
│   └── tool.py
├── contracts/
│   └── dimension_resolution_policy.yml
├── dimension_resolution_cli.py
├── build_dimension_resolution_samples.py
└── generated/
    └── dimension_resolution_samples.json

tests/
└── test_phase5d_dimension_value_resolution.py
```

## 10. Runtime boundary

The resolver itself is read-only. Runtime discovery still uses the Phase 5C safety gate:

```text
PHASE5C_ALLOW_METRICFLOW_DISCOVERY=true
```

The Phase 5D live wrapper additionally requires:

```text
PHASE5D_ALLOW_DIMENSION_RESOLUTION=true
```

Real Spark / Polaris / MetricFlow Runtime evidence remains **DEFERRED** until a workstation runtime is available.
