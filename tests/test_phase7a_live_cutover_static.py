from __future__ import annotations

import os
import subprocess
from pathlib import Path

from metadata.datahub.tools.phase7_runtime import resolve_exact_identities

ROOT = Path(__file__).resolve().parents[1]


class FakeExactGraph:
    def __init__(self, *, instance: str = "commerce_polaris", missing: set[str] | None = None):
        self.instance = instance
        self.missing = missing or set()
        self.queries: list[tuple] = []

    def exists(self, urn: str) -> bool:
        self.queries.append(("exists", urn))
        return urn not in self.missing

    def get_entity_raw(self, urn: str, aspects=None):
        self.queries.append(("get_entity_raw", urn, tuple(aspects or [])))
        return {"dataPlatformInstance": {"instance": self.instance}}


def test_phase7_datahub_identity_resolution_is_exact_urn_only_and_does_not_write_static_truth():
    graph = FakeExactGraph()
    payload = resolve_exact_identities(graph=graph, write=False)
    assert payload["runtime_verified"] is True
    assert all(x["status"] == "RESOLVED_EXPECTED" for x in payload["identities"])
    assert all(x["resolved_urn"] == x["expected_urn"] for x in payload["identities"])
    assert {q[0] for q in graph.queries} <= {"exists", "get_entity_raw"}
    source = (ROOT / "metadata/datahub/tools/phase7_runtime.py").read_text(encoding="utf-8")
    assert ".search(" not in source
    assert "get_results_by_filter" not in source


def test_phase7_datahub_identity_resolution_fails_closed_on_platform_instance_mismatch():
    graph = FakeExactGraph(instance="wrong-instance")
    try:
        resolve_exact_identities(graph=graph, write=False)
    except Exception as exc:
        assert "Exact Dataset identity resolution failed" in str(exc)
    else:
        raise AssertionError("platform-instance mismatch must fail closed")


def test_phase7_datahub_runtime_keeps_phase4_frozen_writers_untouched_and_uses_phase7_adapter():
    text = (ROOT / "metadata/datahub/tools/phase7_runtime.py").read_text(encoding="utf-8")
    assert "DatasetPatchBuilder" in text
    assert "set_domain" in text
    assert "RESOLVED_EXPECTED" in text
    assert "BLOCKED_EXPECTED_URN_NOT_FOUND" in text
    assert "PHASE7A_ALLOW_DATAHUB_GOVERNANCE_WRITE" in text
    # Frozen Phase 4 tools remain explicitly deferred rather than silently upgraded.
    assert "DEFERRED: real DataHub governance mutation" in (ROOT / "metadata/datahub/tools/apply_governance_projection.py").read_text(encoding="utf-8")


def _run_refused(script: str, *, extra_env: dict[str, str] | None = None):
    env = os.environ.copy()
    env.update(extra_env or {})
    proc = subprocess.run(
        ["bash", str(ROOT / script)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    return proc


def test_phase7a_live_wrappers_are_fail_closed_by_default(monkeypatch):
    gates_and_scripts = [
        ("PHASE7A_ALLOW_DATAHUB_BOOTSTRAP", "infra/runtime/run_phase7a_datahub_bootstrap.sh"),
        ("PHASE7A_ALLOW_DATAHUB_GOVERNANCE_WRITE", "infra/runtime/run_phase7a_datahub_acceptance.sh"),
        ("PHASE7A_ALLOW_AGENT_DATAHUB_READ", "infra/runtime/run_phase7a_agent_metadata_live.sh"),
        ("PHASE7A_ALLOW_AGENT_SEMANTIC_RUNTIME", "infra/runtime/run_phase7a_agent_semantic_live.sh"),
        ("PHASE7A_ALLOW_AGENT_DAGSTER_READ", "infra/runtime/run_phase7a_dagster_operational_live.sh"),
        ("PHASE7A_ALLOW_OPENAI_PROVIDER", "infra/runtime/run_phase7a_openai_agent_live.sh"),
    ]
    for gate, script in gates_and_scripts:
        env = {gate: "false"}
        proc = _run_refused(script, extra_env=env)
        assert proc.returncode == 2, (script, proc.stdout, proc.stderr)
        assert "REFUSED" in (proc.stdout + proc.stderr), script


def test_phase7_openai_acceptance_is_renderer_only_and_cannot_gain_tool_authority():
    source = (ROOT / "infra/runtime/phase7/openai_agent_acceptance.py").read_text(encoding="utf-8")
    assert "OpenAIResponsesRenderer" in source
    assert "tool_handles_exposed\": False" in source
    assert "Claim(" in source
    provider = (ROOT / "agent/llm/providers/openai_responses.py").read_text(encoding="utf-8")
    assert "renderer only" in provider
    assert "tool handles" in provider


def test_phase7_dagster_acceptance_is_read_only_exact_partition_truth():
    source = (ROOT / "infra/runtime/phase7/dagster_operational_acceptance.py").read_text(encoding="utf-8")
    assert "DagsterPartitionCompletenessHealthProvider" in source
    assert '"agent_write_authority": "NONE"' in source
    policy = (ROOT / "agent/contracts/operational_runtime_cutover_policy.yml").read_text(encoding="utf-8")
    assert "agent_launch_run: false" in policy
    assert "agent_backfill: false" in policy
    assert "agent_recovery_write: false" in policy
