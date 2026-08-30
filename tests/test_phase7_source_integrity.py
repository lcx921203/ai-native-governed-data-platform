from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "infra/contracts/phase7/phase7_source_manifest.yml"


def load_manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def _paths(spec: dict):
    for key in ("policy", "implementation", "tests"):
        for value in spec.get(key, []) or []:
            yield key, value
    for key in ("static_runner", "live_runner", "acceptance_runner"):
        value = spec.get(key)
        if value:
            yield key, value


def test_phase7_manifest_has_no_missing_declared_source_paths():
    missing = []
    for capability, spec in load_manifest()["capabilities"].items():
        for kind, rel in _paths(spec):
            if not (ROOT / rel).exists():
                missing.append((capability, kind, rel))
    assert missing == []


def test_every_runtime_shell_entrypoint_has_real_shebang_and_no_prefix_bytes():
    bad = []
    for path in sorted((ROOT / "infra/runtime").glob("*.sh")):
        raw = path.read_bytes()
        if not raw.startswith(b"#!/usr/bin/env bash\n"):
            bad.append(str(path.relative_to(ROOT)))
    assert bad == []


def test_phase7_live_gates_default_false_in_env_example():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    gates = {
        gate
        for spec in load_manifest()["capabilities"].values()
        for gate in spec.get("runtime_gates", [])
    }
    for gate in gates:
        assert f"{gate}=false" in env, gate


def test_runtime_evidence_remains_outside_source_truth():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".runtime/" in ignore
    for spec in load_manifest()["capabilities"].values():
        evidence = str(spec.get("runtime_evidence", ""))
        if evidence.startswith(".runtime/"):
            assert not (ROOT / evidence).exists(), f"Runtime evidence leaked into source tree: {evidence}"


def test_final_runtime_runner_only_calls_existing_local_phase7_runners():
    text = (ROOT / "infra/runtime/run_phase7_final_runtime_closure.sh").read_text(encoding="utf-8")
    missing = []
    for token in text.replace("\\\n", " ").split():
        token = token.strip("'\";()")
        if token.startswith("infra/runtime/") and token.endswith(".sh") and not (ROOT / token).exists():
            missing.append(token)
    assert sorted(set(missing)) == []


def test_phase7_contracts_do_not_claim_runtime_verified_source_state():
    for path in sorted((ROOT / "infra/contracts/phase7").glob("*.yml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        status = str(payload.get("status", ""))
        assert status not in {
            "RUNTIME_BOOTSTRAP_VERIFIED",
            "DATAHUB_METADATA_PLANE_VERIFIED",
            "OPENAI_AGENT_RUNTIME_VERIFIED",
            "COMMERCE_MCP_RUNTIME_VERIFIED",
            "PHASE7_END_TO_END_RUNTIME_VERIFIED",
        }, path.name
