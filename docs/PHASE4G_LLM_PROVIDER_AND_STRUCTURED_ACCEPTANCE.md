# Phase 4G · LLM Provider Adapter + Structured Answer Acceptance

## 1. Goal

Phase 4G connects a real model provider **after** Phase 4F has already reduced tool output into a governed `ResponseEnvelope` / Claim Ledger.

```text
User
  ↓
Phase 4E Router
  ↓
Phase 4D Governed Tools
  ↓
Phase 4F Claim Ledger / ResponseEnvelope
  ↓
Phase 4G OpenAI Responses Renderer
  ↓
Structured AnswerDraft
  ↓
Local Evidence Validator
  ↓
User-facing answer
```

The provider is **not an autonomous agent runtime**. It is an answer renderer only.

## 2. Why the Responses API

The adapter uses OpenAI's Responses API. Structured Outputs are supplied under `text.format` with `type: json_schema` and `strict: true`.

The request contains no `tools` field. The LLM cannot invoke DataHub, Dagster, SQL, or the Phase 4D tools from this layer.

## 3. Privacy boundary

Every request is sent with:

```python
store=False
```

This is a deliberate default because the Claim Ledger can contain internal business metadata. The adapter does not expose a configuration switch that silently turns storage back on.

## 4. Structured output is necessary but not sufficient

The provider must return:

```json
{
  "answer": "...",
  "used_claim_ids": ["C01", "C02"],
  "acknowledged_limitations": []
}
```

The provider-side JSON Schema proves the **shape** of the output. The local validator then proves the draft is authorized by this exact envelope:

- every claim id must exist in the envelope;
- claim ids must be unique and no more than 8;
- acknowledged limitations must come from the envelope;
- `DEFERRED` / `PARTIAL` / `NEEDS_DISCOVERY` must preserve a real limitation;
- `BLOCKED` cannot use substantive claims;
- a `DEFERRED` answer cannot consume runtime-observation claims.

Therefore:

```text
Structured Output
≠
Grounded Answer

Structured Output
+
Local Evidence Validation
=
Accepted AnswerDraft
```

## 5. Prompt-injection boundary

The system instruction explicitly treats both the original user question and claim text as **data, not instructions**. The user question is still carried for language/context, but it cannot grant the provider new permissions.

## 6. Provider configuration

```bash
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.6-terra
OPENAI_MAX_OUTPUT_TOKENS=1200
PHASE4G_ALLOW_OPENAI_CALL=false
```

Live execution requires both:

```text
OPENAI_API_KEY
+
PHASE4G_ALLOW_OPENAI_CALL=true
```

Without both, the adapter fails closed before making a network request.

The default model is intentionally configurable. Phase 4G's task is constrained answer rendering rather than open-ended reasoning.

## 7. CLI

Static/provider-free path remains the default:

```bash
PYTHONPATH=. python agent/answer_cli.py \
  "activity_net_sales 是什么意思？"
```

Live provider path, only after runtime configuration:

```bash
PHASE4G_ALLOW_OPENAI_CALL=true \
PYTHONPATH=. python agent/answer_cli.py \
  "activity_net_sales 是什么意思？" \
  --renderer openai
```

## 8. Failure handling

These are explicit failures, not silent fallbacks:

```text
provider refusal          → FAIL CLOSED
incomplete response       → FAIL CLOSED
malformed JSON            → FAIL CLOSED
unknown claim id          → FAIL CLOSED
invented limitation       → FAIL CLOSED
missing required runtime limitation → FAIL CLOSED
```

The deterministic renderer remains available as a separate explicit mode. Phase 4G does not silently substitute deterministic output for a failed live provider call, because doing so would hide the operational distinction between "LLM succeeded" and "LLM unavailable".

## 9. Evidence status

Static acceptance can prove:

- request shape;
- no tool / SQL exposure;
- `store=false`;
- strict Structured Output schema construction;
- response/refusal/incomplete handling;
- claim-id and limitation revalidation;
- hard live-call gate.

It cannot prove:

- a real OpenAI API request succeeds;
- model latency / token usage;
- real multilingual answer quality;
- real provider refusal behavior;
- production credentials and network path.

Those remain **DEFERRED Runtime Acceptance** until a workstation/API-key environment is available.

## 10. Static closure result

Current Phase 4G focused closure:

```text
97 / 97 PASS
```

Full project regression after Phase 4G:

```text
199 / 199 PASS
```

The live wrapper was also checked in the default environment and correctly refused execution while `PHASE4G_ALLOW_OPENAI_CALL=false`.

## 11. Official references

- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- OpenAI Responses migration guide: https://developers.openai.com/api/docs/guides/migrate-to-responses
- OpenAI model catalog: https://developers.openai.com/api/docs/models
- Official OpenAI Python SDK: https://github.com/openai/openai-python
