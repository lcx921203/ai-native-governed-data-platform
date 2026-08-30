from __future__ import annotations

import json
from pathlib import Path

from agent.adapters.datahub_sdk import ExactDataHubReadAdapter
from agent.metadata_runtime import DataHubMetadataRuntime
from infra.runtime.phase7.runtime_helpers import ROOT, require_gate, write_verified_evidence


def main() -> int:
    require_gate("PHASE7A_ALLOW_AGENT_DATAHUB_READ")
    try:
        from datahub.sdk import DataHubClient
    except ImportError as exc:
        raise SystemExit(f"DEFERRED: DataHub SDK unavailable: {exc}")
    client = DataHubClient.from_env()
    adapter = ExactDataHubReadAdapter(client)
    runtime = DataHubMetadataRuntime(ROOT, adapter=adapter)
    binding, warning = runtime.binding("orders")
    if binding is None:
        raise SystemExit(f"DEFERRED: {warning}")
    entity = adapter.get_dataset(binding.urn)
    if entity is None:
        raise SystemExit("DEFERRED: exact DataHub Dataset read returned no entity")
    output = write_verified_evidence(
        ".runtime/evidence/phase7a/agent_metadata_runtime.json",
        status="AGENT_METADATA_RUNTIME_CUTOVER_VERIFIED",
        details={
            "contract": "commerce_phase7a_agent_metadata_runtime",
            "dataset": binding.dataset,
            "urn": binding.urn,
            "read_mode": "EXACT_DATASET_URN_READ_ONLY",
            "datahub_mutation_exposed": False,
        },
    )
    print(json.dumps({"status": "AGENT_METADATA_RUNTIME_CUTOVER_VERIFIED", "evidence": str(output.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
