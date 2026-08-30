from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(rel: str):
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def policy_runtime_gates(policy: dict) -> set[str]:
    runtime = policy.get("runtime", {})
    gates: set[str] = set()
    allow = runtime.get("allow_env")
    if isinstance(allow, str):
        gates.add(allow)
    for key, value in runtime.items():
        if key.startswith("requires_") and key.endswith("_gate") and isinstance(value, str):
            gates.add(value)
    return gates


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def test_phase6_is_frozen_at_6a_through_6f_and_points_to_phase7_runtime():
    manifest = load_yaml("agent/contracts/phase6_capability_manifest.yml")
    assert manifest["version"] == 2
    assert manifest["mode"] == "final_engineering_static_closure"
    assert set(manifest["phases"]) == {"6A", "6B", "6C", "6D", "6E", "6F"}
    closure = manifest["closure"]
    assert closure["status"] == "STATIC_ENGINEERING_CLOSED"
    assert closure["frozen_scope"] == ["6A", "6B", "6C", "6D", "6E", "6F"]
    assert closure["next_phase"] == "PHASE7_REAL_RUNTIME"
    assert closure["runtime_evidence"] == "DEFERRED"


def test_phase6_authority_matrix_has_no_agent_production_write_authority():
    closure = load_yaml("agent/contracts/phase6_capability_manifest.yml")["closure"]
    authority = closure["production_write_authority"]
    assert authority["semantic_calculation"] == "DBT_METRICFLOW"
    assert authority["automated_recovery_execution"] == "PHASE3C_DAGSTER_RECOVERY_SENSOR"
    assert authority["incident_response_planning"] == "PHASE6E_ADVISORY_ONLY"
    assert authority["approval_state_and_audit"] == "PHASE6F_APPROVAL_ONLY"
    assert authority["agent_production_write"] == "NONE"


def test_policy_runtime_gate_dependencies_match_capability_manifest_and_live_wrappers():
    manifest = load_yaml("agent/contracts/phase6_capability_manifest.yml")
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for phase, item in manifest["phases"].items():
        policy = load_yaml(item["policy"])
        expected = set(item["runtime_gates"])
        assert policy_runtime_gates(policy) == expected, phase
        wrapper = (ROOT / item["live_runner"]).read_text(encoding="utf-8")
        for gate in expected:
            assert gate in wrapper, f"{phase}: live wrapper does not enforce {gate}"
            assert f"{gate}=false" in env_text, f"{phase}: {gate} is not fail-closed in .env.example"


def test_phase6_policies_do_not_create_a_second_sql_or_recovery_execution_engine():
    manifest = load_yaml("agent/contracts/phase6_capability_manifest.yml")
    for phase, item in manifest["phases"].items():
        policy = load_yaml(item["policy"])
        principles = policy.get("principles", {})
        assert principles.get("arbitrary_sql") is False, phase
    response = load_yaml("agent/contracts/incident_response_policy.yml")
    approval = load_yaml("agent/contracts/approval_workflow_policy.yml")
    assert response["runtime"]["writes_enabled"] is False
    assert approval["runtime"]["production_action_writes_enabled"] is False
    assert response["principles"]["phase3c_recovery_policy_is_execution_authority"] is True
    assert approval["principles"]["approval_is_not_execution"] is True


def test_phase6_claim_ledger_has_all_diagnostic_incident_and_approval_claim_kinds():
    from agent.response import ClaimKind

    required = {
        "ANOMALY_OBSERVATION",
        "OPERATIONAL_HEALTH",
        "DIAGNOSTIC_CLASSIFICATION",
        "DRIVER_ATTRIBUTION",
        "INCIDENT_EVIDENCE",
        "RECOVERY_STATUS",
        "INCIDENT_RESPONSE_PLAN",
        "ACTION_AUTHORITY",
        "APPROVAL_STATUS",
        "APPROVAL_AUDIT",
    }
    assert required <= {item.value for item in ClaimKind}


def test_runtime_observation_validator_is_part_of_the_frozen_evidence_boundary():
    text = (ROOT / "agent/response/validator.py").read_text(encoding="utf-8")
    assert "claim.runtime_observed and claim.evidence != \"RUNTIME_VERIFIED\"" in text
    assert "Runtime-observed claim" in text


def test_phase6_response_and_approval_modules_have_no_production_execution_handles():
    forbidden = (
        "RunRequest(",
        "submit_run(",
        "create_run(",
        "execute_job(",
        "execute_in_process(",
        "subprocess.run(",
        "os.system(",
    )
    targets = [
        ROOT / "agent/incident_response/planner.py",
        ROOT / "agent/approval_workflow/workflow.py",
        ROOT / "agent/approval_workflow/response.py",
    ]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if path.name == "workflow.py":
                # Phase 6F intentionally lists forbidden symbols once as a self-audit constant.
                assert text.count(token) <= 1, f"{path}: callable production execution handle {token}"
            else:
                assert token not in text, f"{path}: forbidden production execution handle {token}"
    audit_store = (ROOT / "agent/approval_workflow/store.py").read_text(encoding="utf-8")
    for token in ("import dagster", "RunRequest(", "submit_run(", "execute_job(", "subprocess", "os.system("):
        assert token not in audit_store


def test_phase6_contract_lock_remains_historical_evidence_while_current_source_may_evolve():
    """Phase 6 lock 保存历史哈希，但不把当前 canonical tree 冻结成只读。

    当前源码允许继续实现功能和补充注释；如果某个 Phase 6-origin 文件已经演进，
    provenance 必须明确标为 current-source evolution，而不能继续声称 byte-for-byte Phase 6。
    """
    import csv

    lock_path = ROOT / "infra/contracts/phase6/phase6_static_closure_lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["closure_status"] == "STATIC_ENGINEERING_CLOSED"
    assert lock["runtime_evidence"] == "DEFERRED"

    with (ROOT / "FULL_SOURCE_PROVENANCE.csv").open(encoding="utf-8", newline="") as fh:
        provenance = {row["path"]: row for row in csv.DictReader(fh)}

    for rel, historical_sha in lock["sha256"].items():
        path = ROOT / rel
        assert path.exists(), rel
        current_sha = sha256(path)
        if current_sha == historical_sha:
            continue
        row = provenance.get(rel)
        assert row is not None, f"Current-source evolution lacks provenance: {rel}"
        assert row["provenance"] == "POST_BASELINE_USER_REQUEST_2026_08_20", rel
        assert "current canonical source evolution" in row["source_authority"], rel
        assert "historical" in row["note"].lower(), rel


def test_source_state_and_closure_doc_describe_phase6_as_static_closed_not_runtime_verified():
    source_state = (ROOT / "SOURCE_STATE.md").read_text(encoding="utf-8")
    closure_doc = (ROOT / "docs/PHASE6_CLOSURE_AUDIT.md").read_text(encoding="utf-8")
    assert "Phase 6 Final Static Closure Snapshot" in source_state
    assert "STATIC_ENGINEERING_CLOSED" in source_state
    assert "RUNTIME_VERIFIED" in source_state
    assert "DEFERRED" in source_state
    assert "Phase 7" in closure_doc
    assert "DEFERRED" in closure_doc


def test_shopify_source_contract_remains_outside_phase6_agent_responsibility():
    source = load_yaml("dbt/mercaso_dbt/models/sources/shopify.yml")
    shopify = source["sources"][0]
    assert shopify["name"] == "shopify"
    identifiers = {table["identifier"] for table in shopify["tables"]}
    assert identifiers == {
        "shopify_order",
        "shopify_order_item",
        "shopify_line_item_discount_allocation",
        "shopify_transaction",
        "shopify_refund",
        "shopify_refund_item",
        "shopify_refund_transaction",
        "shopify_fulfillment",
        "shopify_fulfillment_item",
        "shopify_fulfillment_event",
    }
