# Phase 4F — Governed Response Envelope + Constrained LLM Boundary

## 1. Why this phase exists

Phase 4D established governed read tools. Phase 4E established deterministic intent and
minimum-tool planning. The next unsafe shortcut would be to pass raw tool payloads to an
LLM and ask it to “answer naturally”. That makes it easy to blur three very different
truth levels:

- design-time/static contract truth;
- runtime-verified observations;
- information that is currently unavailable.

Phase 4F adds an explicit evidence boundary before any external LLM provider is connected.

```text
User question
    ↓
Phase 4E deterministic router
    ↓
Phase 4D governed tools
    ↓
PlanExecution
    ↓
Phase 4F GovernedResponseComposer
    ↓
Claim Ledger + Evidence + Limitations
    ↓
Constrained renderer
    ↓
AnswerDraft + used_claim_ids
    ↓
Answer validator
```

The LLM is deliberately **not** a tool client in this design. It receives only the governed
claims that have already passed through routing, tool allowlists, and evidence policy.

## 2. The key distinction: Tool Result is not an Answer

A tool result can contain useful metadata and also contain caveats. For example, the
runtime tool currently knows the static automation contract:

- job: `shopify_daily_partition_job`
- schedule: `00:15 UTC`
- freshness deadline: `01:00 UTC`
- freshness budget: `45 minutes`

But without a real Dagster instance it cannot know what happened yesterday. Therefore the
response envelope may contain an `AUTOMATION_CONTRACT` claim, while it must not contain a
`RUNTIME_OBSERVATION` claim.

This prevents the answer layer from turning:

```text
Schedule contract exists
```

into the unsupported statement:

```text
Yesterday's run was late or failed.
```

## 3. Claim Ledger

Every allowed factual statement is converted into a claim:

```json
{
  "id": "C02",
  "kind": "FORMULA",
  "text": "activity_net_sales = sales_before_reversal - sales_reversal_amount",
  "evidence": "STATIC_CONTRACT",
  "source_locations": [
    "dbt/mercaso_dbt/models/metrics/sales.yml"
  ],
  "runtime_observed": false
}
```

Claim IDs are stable within a single envelope and are the only facts a future LLM renderer
is allowed to declare as used.

## 4. Evidence levels

Phase 4F keeps the same evidence vocabulary as the governed tools:

| Evidence | Meaning |
|---|---|
| `STATIC_CONTRACT` | Proven from versioned code/configuration, not observed from a live runtime |
| `RUNTIME_VERIFIED` | Observed from the real runtime / metadata system |
| `DEFERRED` | Runtime evidence is not currently available |

Hard rule:

```text
runtime_observed = true
        requires
Evidence = RUNTIME_VERIFIED
```

A `DEFERRED` envelope is rejected if it contains a runtime-observation claim.

## 5. Answer states

The final response layer has explicit states:

- `ANSWERED` — the governed evidence is sufficient for the requested definition/context;
- `PARTIAL` — some useful facts exist but the requested answer is incomplete;
- `NEEDS_DISCOVERY` — no unique governed subject is resolved;
- `DEFERRED` — the question requires runtime evidence that is not available;
- `BLOCKED` — the request is outside the read-only governed Agent boundary;
- `ERROR` — fail closed.

`DEFERRED`, `PARTIAL`, and `NEEDS_DISCOVERY` answers must preserve a limitation claim. The
renderer is not allowed to silently drop it.

## 6. Constrained LLM contract

Phase 4F is provider-neutral. It defines the interface but does not yet bind the project to
OpenAI, Anthropic, or another SDK.

The future renderer receives only:

```text
question
intent
answer status
resolved subject
approved claims
limitations
```

It does **not** receive:

```text
arbitrary DataHub access
SQL execution
raw tool objects
hidden repository handles
```

The renderer must return:

```json
{
  "answer": "...",
  "used_claim_ids": ["C01", "C02"],
  "acknowledged_limitations": []
}
```

`agent/response/validator.py` rejects unknown claim IDs and rejects a non-final answer that
fails to acknowledge its evidence limitation.

## 7. Deterministic renderer

Until an external LLM provider is connected, `render_deterministic()` produces a simple
answer from the same envelope. This is intentionally not the final UX. Its purpose is to
prove the end-to-end contract without depending on a model or API key.

Example:

```bash
PYTHONPATH=. python agent/answer_cli.py \
  "activity_net_sales 是什么意思？"
```

Runtime example:

```bash
PYTHONPATH=. python agent/answer_cli.py \
  "为什么 orders 昨天没更新？"
```

The second answer may describe the schedule contract, but it must also say that actual
Dagster run/failure/recovery facts are unavailable and runtime diagnosis is deferred.

## 8. Files

```text
agent/
├── response/
│   ├── contracts.py
│   ├── composer.py
│   ├── render.py
│   └── validator.py
├── llm/
│   ├── contracts.py
│   └── prompt.py
├── contracts/
│   ├── answer_policy.yml
│   └── llm_answer_schema.json
├── answer_cli.py
├── build_answer_samples.py
└── generated/
    └── answer_samples.json

tests/
└── test_phase4f_governed_response.py

infra/runtime/
└── run_phase4f_governed_response_static.sh
```

## 9. Evidence boundary

Phase 4F proves:

- the answer layer uses a bounded claim ledger;
- formulas remain sourced from dbt / MetricFlow;
- static governance is labeled static;
- static lineage fallback remains labeled static;
- a runtime question cannot manufacture a runtime observation;
- a future LLM cannot cite an unknown claim ID;
- blocked SQL does not become an alternate execution path.

It does **not** prove:

- a real LLM API call;
- model quality or natural-language routing quality in production;
- real DataHub runtime reads;
- real Dagster run-history diagnosis.

Those remain separate runtime/provider acceptance steps.
