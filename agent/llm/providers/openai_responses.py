"""OpenAI Responses API adapter for the governed answer-rendering boundary.

This module is deliberately *renderer only*.  It receives a pre-approved
ResponseEnvelope, sends only the bounded claim payload to OpenAI, parses a Structured
Output into AnswerDraft, and runs the local evidence validator before returning.

It never receives tool handles, SQL access, DataHub clients, or Dagster clients.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.llm.prompt import build_renderer_payload
from agent.llm.usage import LLMUsageEvent, record_llm_usage
from agent.response.contracts import AnswerDraft, ResponseEnvelope
from agent.response.validator import validate_answer_draft


class OpenAIProviderError(RuntimeError):
    """Base class for fail-closed provider errors."""


class OpenAIProviderUnavailable(OpenAIProviderError):
    """Raised when live OpenAI execution is not explicitly enabled/configured."""


class OpenAIProviderRefusal(OpenAIProviderError):
    """Raised when the model returns a safety refusal rather than schema output."""


class OpenAIProviderIncomplete(OpenAIProviderError):
    """Raised when the Responses API does not complete the structured answer."""


@dataclass(frozen=True)
class OpenAIProviderConfig:
    model: str = "gpt-5.6-terra"
    max_output_tokens: int = 1200

    @classmethod
    def from_env(cls, *, require_live_gate: bool = True) -> "OpenAIProviderConfig":
        if require_live_gate and os.getenv("PHASE4G_ALLOW_OPENAI_CALL", "false").lower() != "true":
            raise OpenAIProviderUnavailable(
                "Live OpenAI calls are disabled. Set PHASE4G_ALLOW_OPENAI_CALL=true explicitly."
            )
        if not os.getenv("OPENAI_API_KEY"):
            raise OpenAIProviderUnavailable("OPENAI_API_KEY is not configured")

        raw_tokens = os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "1200")
        try:
            max_output_tokens = int(raw_tokens)
        except ValueError as exc:
            raise OpenAIProviderUnavailable("OPENAI_MAX_OUTPUT_TOKENS must be an integer") from exc
        if not 128 <= max_output_tokens <= 4000:
            raise OpenAIProviderUnavailable(
                "OPENAI_MAX_OUTPUT_TOKENS must stay within the governed range [128, 4000]"
            )

        return cls(
            model=os.getenv("OPENAI_MODEL", "gpt-5.6-terra").strip() or "gpt-5.6-terra",
            max_output_tokens=max_output_tokens,
        )


def _root_from_module() -> Path:
    return Path(__file__).resolve().parents[3]


def load_local_answer_schema(root: Path | None = None) -> dict[str, Any]:
    root = root or _root_from_module()
    return json.loads((root / "agent/contracts/llm_answer_schema.json").read_text())


def build_openai_answer_schema(root: Path | None = None) -> dict[str, Any]:
    """Build the provider schema from the canonical local contract.

    `uniqueItems` is intentionally enforced locally rather than sent to the provider.
    This keeps the provider schema on the explicitly documented Structured Outputs
    subset while preserving the stronger local contract in `validate_provider_payload`.
    """

    schema = deepcopy(load_local_answer_schema(root))
    schema.pop("$schema", None)
    schema.pop("title", None)

    def strip_local_only(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("uniqueItems", None)
            for value in node.values():
                strip_local_only(value)
        elif isinstance(node, list):
            for value in node:
                strip_local_only(value)

    strip_local_only(schema)
    return schema


def build_openai_request(
    envelope: ResponseEnvelope,
    config: OpenAIProviderConfig,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    payload = build_renderer_payload(envelope)
    system_rules = str(payload.pop("system_rules"))

    return {
        "model": config.model,
        "input": [
            {
                "role": "system",
                "content": system_rules,
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "commerce_governed_answer",
                "strict": True,
                "schema": build_openai_answer_schema(root),
            }
        },
        # Governed metadata may contain internal business context. Do not persist the
        # response server-side by default.
        "store": False,
        "max_output_tokens": config.max_output_tokens,
    }


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _int(obj: Any, name: str) -> int:
    value = _get(obj, name, 0)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _record_usage(response: Any, *, requested_model: str) -> None:
    """记录 Provider 实际返回的 usage；不记录 Prompt 或 API Key。"""

    usage = _get(response, "usage")
    if usage is None:
        return

    input_details = _get(usage, "input_tokens_details") or {}
    output_details = _get(usage, "output_tokens_details") or {}

    event = LLMUsageEvent(
        provider="openai",
        model=str(_get(response, "model", requested_model) or requested_model),
        input_tokens=_int(usage, "input_tokens"),
        output_tokens=_int(usage, "output_tokens"),
        total_tokens=_int(usage, "total_tokens"),
        cached_input_tokens=_int(input_details, "cached_tokens"),
        cache_write_tokens=_int(input_details, "cache_write_tokens"),
        reasoning_tokens=_int(output_details, "reasoning_tokens"),
        response_id=str(_get(response, "id", "") or ""),
    )
    record_llm_usage(event)


def _find_refusal(response: Any) -> str | None:
    for output in _get(response, "output", []) or []:
        if _get(output, "type") != "message":
            continue
        for item in _get(output, "content", []) or []:
            if _get(item, "type") == "refusal":
                refusal = _get(item, "refusal", "")
                if refusal:
                    return str(refusal)
    return None


def validate_provider_payload(payload: Any) -> AnswerDraft:
    """Parse the provider JSON payload into the provider-neutral AnswerDraft contract."""

    if not isinstance(payload, dict):
        raise OpenAIProviderError("Structured answer payload must be a JSON object")

    required = {"answer", "used_claim_ids", "acknowledged_limitations"}
    if set(payload) != required:
        raise OpenAIProviderError(
            "Structured answer payload keys must be exactly: " + ", ".join(sorted(required))
        )

    answer = payload["answer"]
    used_claim_ids = payload["used_claim_ids"]
    acknowledged = payload["acknowledged_limitations"]

    if not isinstance(answer, str) or not answer.strip():
        raise OpenAIProviderError("Structured answer must contain a non-empty string answer")
    if not isinstance(used_claim_ids, list) or not all(isinstance(x, str) for x in used_claim_ids):
        raise OpenAIProviderError("used_claim_ids must be a string array")
    if len(used_claim_ids) > 8:
        raise OpenAIProviderError("used_claim_ids exceeds governed max of 8")
    if len(set(used_claim_ids)) != len(used_claim_ids):
        raise OpenAIProviderError("used_claim_ids must not contain duplicates")
    if any(re.fullmatch(r"C[0-9]{2}", claim_id) is None for claim_id in used_claim_ids):
        raise OpenAIProviderError("used_claim_ids contains an invalid claim id")

    if not isinstance(acknowledged, list) or not all(isinstance(x, str) for x in acknowledged):
        raise OpenAIProviderError("acknowledged_limitations must be a string array")
    if len(set(acknowledged)) != len(acknowledged):
        raise OpenAIProviderError("acknowledged_limitations must not contain duplicates")

    return AnswerDraft(
        answer=answer.strip(),
        used_claim_ids=tuple(used_claim_ids),
        acknowledged_limitations=tuple(acknowledged),
    )


class OpenAIResponsesRenderer:
    """Live OpenAI renderer with dependency injection for static acceptance tests."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        config: OpenAIProviderConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.root = root or _root_from_module()
        self.config = config or OpenAIProviderConfig.from_env()
        self._client = client

    def _client_or_create(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except (ImportError, AttributeError) as exc:  # local static env may not install SDK
            raise OpenAIProviderUnavailable(
                "OpenAI Python SDK is unavailable. Install requirements-agent.txt first."
            ) from exc
        self._client = OpenAI()
        return self._client

    def render(self, envelope: ResponseEnvelope) -> AnswerDraft:
        request = build_openai_request(envelope, self.config, root=self.root)
        try:
            response = self._client_or_create().responses.create(**request)
        except OpenAIProviderError:
            raise
        except Exception as exc:  # Provider/network errors are fail-closed at this boundary.
            raise OpenAIProviderError(f"OpenAI Responses API call failed: {exc}") from exc

        # Usage 必须在 refusal / incomplete / schema validation 之前记录：
        # Provider 已经完成了计费用量，即使最终 Agent 因验证失败而 Fail Closed。
        _record_usage(response, requested_model=self.config.model)

        refusal = _find_refusal(response)
        if refusal:
            raise OpenAIProviderRefusal(refusal)

        status = _get(response, "status")
        if status != "completed":
            details = _get(response, "incomplete_details")
            reason = _get(details, "reason", "unknown") if details is not None else "unknown"
            raise OpenAIProviderIncomplete(f"OpenAI response status={status!r}, reason={reason!r}")

        output_text = _get(response, "output_text", "")
        if not output_text:
            raise OpenAIProviderError("Completed OpenAI response did not contain output_text")

        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise OpenAIProviderError("OpenAI structured output was not valid JSON") from exc

        draft = validate_provider_payload(payload)
        # The provider schema proves shape; the local validator proves the draft is actually
        # authorized by this exact evidence envelope.
        validate_answer_draft(envelope, draft)
        return draft
