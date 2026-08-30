from __future__ import annotations

import json
import os

from agent.llm.providers.openai_responses import OpenAIProviderConfig, OpenAIResponsesRenderer
from agent.response.contracts import AnswerStatus, Claim, ClaimKind, ResponseEnvelope
from infra.runtime.phase7.runtime_helpers import ROOT, require_gate, write_verified_evidence


def main() -> int:
    require_gate("PHASE7A_ALLOW_OPENAI_PROVIDER")
    # Reuse the frozen renderer-only Phase 4G boundary.  Phase 7's gate is the outer
    # authority; the legacy provider gate remains explicit rather than being deleted.
    os.environ["PHASE4G_ALLOW_OPENAI_CALL"] = "true"
    if os.getenv("OPENAI_AGENT_MODEL"):
        os.environ["OPENAI_MODEL"] = os.environ["OPENAI_AGENT_MODEL"]
    envelope = ResponseEnvelope(
        question="activity_net_sales 是什么意思？",
        intent="METRIC_DEFINITION",
        status=AnswerStatus.ANSWERED,
        subject={"kind": "metric", "id": "activity_net_sales"},
        claims=[
            Claim(
                id="C01",
                kind=ClaimKind.DEFINITION,
                text="Activity Net Sales is net sales viewed by the actual business activity times of sales and reversals.",
                evidence="STATIC_CONTRACT",
                source_locations=("metadata/datahub/governance/metric_registry.yml",),
                runtime_observed=False,
            ),
            Claim(
                id="C02",
                kind=ClaimKind.FORMULA,
                text="activity_net_sales = sales_before_reversal - sales_reversal_amount",
                evidence="STATIC_CONTRACT",
                source_locations=("dbt/mercaso_dbt/models/metrics/sales.yml",),
                runtime_observed=False,
            ),
        ],
    )
    config = OpenAIProviderConfig.from_env(require_live_gate=True)
    draft = OpenAIResponsesRenderer(root=ROOT, config=config).render(envelope)
    output = write_verified_evidence(
        ".runtime/evidence/phase7a/openai_agent_runtime.json",
        status="OPENAI_AGENT_RUNTIME_VERIFIED",
        details={
            "contract": "commerce_phase7a_openai_agent_runtime",
            "provider": "openai_responses",
            "model": config.model,
            "used_claim_ids": list(draft.used_claim_ids),
            "renderer_only": True,
            "tool_handles_exposed": False,
            "claim_ledger_is_fact_authority": True,
        },
    )
    print(json.dumps({"status": "OPENAI_AGENT_RUNTIME_VERIFIED", "evidence": str(output.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
