from __future__ import annotations

import json
import os

from agent.diagnostic.operational_health_current import DagsterPartitionCompletenessHealthProvider
from agent.semantic_query.contracts import SemanticQuerySpec
from infra.runtime.phase7.runtime_helpers import ROOT, require_gate, require_verified_evidence, write_verified_evidence


def main() -> int:
    require_gate("PHASE7A_ALLOW_AGENT_DAGSTER_READ")
    require_verified_evidence(".runtime/evidence/phase7a/core_runtime.json", "RUNTIME_BOOTSTRAP_VERIFIED")
    day = os.getenv("PHASE7_ACCEPTANCE_DATE", "2026-08-05")
    spec = SemanticQuerySpec(
        metric="gross_sales",
        start_time=f"{day}T00:00:00Z",
        end_time=f"{day}T23:59:59Z",
        limit=5,
    )
    snapshot = DagsterPartitionCompletenessHealthProvider(ROOT).snapshot(spec)
    if snapshot.evidence != "RUNTIME_VERIFIED":
        raise SystemExit(f"DEFERRED: Dagster exact-partition read is unavailable: {snapshot.details}")
    output = write_verified_evidence(
        ".runtime/evidence/phase7a/dagster_operational_runtime.json",
        status="DAGSTER_OPERATIONAL_RUNTIME_VERIFIED",
        details={
            "contract": "commerce_phase7a_dagster_operational_runtime",
            "partition": day,
            "health_state": snapshot.state.value,
            "source": snapshot.source,
            "details": snapshot.details,
            "execution_authority": "phase3c_recovery_policy",
            "agent_write_authority": "NONE",
        },
    )
    print(json.dumps({"status": "DAGSTER_OPERATIONAL_RUNTIME_VERIFIED", "evidence": str(output.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
