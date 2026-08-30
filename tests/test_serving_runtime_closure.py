from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_final_runtime_closure_includes_serving_and_consumer_governance():
    contract = yaml.safe_load(
        (ROOT / "infra/contracts/phase7/phase7_final_closure.yml").read_text(encoding="utf-8")
    )
    evidence = contract["required_evidence"]
    assert len(evidence) == 13
    assert evidence["serving_runtime"] == {
        "path": ".runtime/evidence/serving/serving_runtime.json",
        "status": "SERVING_RUNTIME_VERIFIED",
    }
    assert evidence["serving_governance"] == {
        "path": ".runtime/evidence/serving/datahub/serving_governance_runtime.json",
        "status": "SERVING_GOVERNANCE_RUNTIME_VERIFIED",
    }
    assert contract["authority_audit"]["serving_query_authority"] == "trino"


def test_serving_runtime_acceptance_is_runtime_gated_and_reconciles_api_with_trino():
    source = (ROOT / "infra/runtime/serving_runtime_acceptance.py").read_text(encoding="utf-8")
    assert "SERVING_ALLOW_RUNTIME_ACCEPTANCE" in source
    assert "SERVING_RUNTIME_VERIFIED" in source
    assert "bi_daily_executive$snapshots" in source
    assert "Trino/API row-count mismatch" in source

    runner = (ROOT / "infra/runtime/run_serving_runtime.sh").read_text(encoding="utf-8")
    assert "SERVING_ALLOW_RUNTIME_ACCEPTANCE" in runner
    assert "serving_runtime_acceptance.py" in runner


def test_serving_governance_verify_all_writes_final_runtime_evidence():
    source = (ROOT / "metadata/datahub/tools/serving_runtime.py").read_text(encoding="utf-8")
    assert "verify_full_serving_governance_runtime" in source
    assert "SERVING_GOVERNANCE_RUNTIME_VERIFIED" in source
    assert "upstreamLineage" in source
    assert "final re-query failed" in source
    assert '"verify-all"' in source


def test_final_runtime_runner_executes_serving_before_13_component_aggregation():
    runner = (ROOT / "infra/runtime/run_phase7_final_runtime_closure.sh").read_text(encoding="utf-8")
    assert "SERVING_ACCEPTANCE_PARTITION_KEY" in runner
    assert "run_serving_runtime.sh" in runner
    assert "run_serving_governance_runtime.sh" in runner
    assert "13 份 evidence" in runner


def test_serving_governance_runner_uses_datahub_venv_and_exact_endpoint_urns():
    runner = (ROOT / "infra/runtime/run_serving_governance_runtime.sh").read_text(encoding="utf-8")
    assert ".venv-datahub/bin/datahub" in runner
    assert "SERVING_API_EXECUTIVE_DAILY_URN" in runner
    assert "SERVING_API_REGION_DAILY_URN" in runner
    assert "resolve_serving_consumer_identities.py" in runner
    assert "verify-all" in runner
