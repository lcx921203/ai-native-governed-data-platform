from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_contract() -> dict:
    return yaml.safe_load((ROOT / "infra/contracts/phase7/runtime_bootstrap.yml").read_text(encoding="utf-8"))


def test_phase7a_is_runtime_bootstrap_not_static_feature_expansion():
    contract = load_contract()
    assert contract["version"] == 1
    assert contract["mode"] == "workstation_runtime_bootstrap"
    assert contract["status"] == "ENGINEERED_RUNTIME_EXECUTION_DEFERRED"
    assert contract["principles"]["phase6_final_static_closure_is_prerequisite"] is True
    assert contract["principles"]["static_contracts_do_not_upgrade_runtime_evidence"] is True


def test_core_runtime_bootstrap_is_fail_closed_by_explicit_gate():
    contract = load_contract()
    assert contract["runtime"]["allow_env"] == "PHASE7A_ALLOW_RUNTIME_BOOTSTRAP"
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    runner = (ROOT / "infra/runtime/run_phase7a_core_bootstrap.sh").read_text(encoding="utf-8")
    assert "PHASE7A_ALLOW_RUNTIME_BOOTSTRAP=false" in env
    assert "PHASE7A_ALLOW_RUNTIME_BOOTSTRAP" in runner
    assert "REFUSED" in runner
    assert "exit 2" in runner


def test_phase7a_bootstrap_order_preserves_phase6_and_semantic_runtime_boundaries():
    order = load_contract()["bootstrap_order"]
    assert order == [
        "phase6_static_closure",
        "workstation_preflight",
        "core_data_plane",
        "spark_polaris_iceberg_rustfs_acceptance",
        "canonical_dbt_build",
        "metricflow_compatibility_query_acceptance",
        "dagster_runtime_environment",
        "dagster_definition_and_phase3c_preflight",
        "runtime_evidence_snapshot",
    ]


def test_phase7a_core_services_match_compose_services():
    contract = load_contract()
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(contract["core_services"]) <= set(compose["services"])


def test_runtime_images_are_pinned_by_default_not_latest():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for expected in (
        "rustfs/rustfs:1.0.0-beta.8",
        "apache/polaris:1.7.0",
        "apache/spark:3.5.6-java17",
        "amazon/aws-cli:2.36.8",
        "alpine/curl:8.21.0",
    ):
        assert expected in compose
    assert ":latest" not in compose


def test_phase7a_keeps_dbt_metricflow_and_dagster_in_isolated_venvs():
    components = load_contract()["components"]
    assert components["dbt"]["venv"] == ".venv-dbt"
    assert components["metricflow"]["venv"] == ".venv-mf"
    assert components["dagster"]["venv"] == ".venv-dagster"
    assert len({components[name]["venv"] for name in ("dbt", "metricflow", "dagster")}) == 3


def test_datahub_is_deliberately_outside_core_bootstrap_not_falsely_runtime_verified():
    datahub = load_contract()["components"]["datahub"]
    assert datahub["status"] == "PENDING_PHASE7A_METADATA_BOOTSTRAP"
    assert "not started" in datahub["note"]


def test_preflight_has_no_runtime_evidence_upgrade_path():
    text = (ROOT / "infra/runtime/phase7/phase7a_preflight.py").read_text(encoding="utf-8")
    assert '"runtime_verified": False' in text
    assert "READY_FOR_BOOTSTRAP" in text
    assert "RUNTIME_VERIFIED" in text


def test_runtime_evidence_is_gitignored_and_not_source_truth():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    contract = load_contract()
    assert ".runtime/" in ignore
    assert contract["principles"]["runtime_evidence_is_written_outside_source_contracts"] is True


def test_all_runtime_shell_entrypoints_have_real_shebang_first_line():
    bad = []
    for path in sorted((ROOT / "infra/runtime").glob("*.sh")):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        if not first.startswith("#!"):
            bad.append(str(path.relative_to(ROOT)))
    assert bad == []


def test_phase7a_runtime_evidence_collector_requires_all_core_tools_to_pass():
    text = (ROOT / "infra/runtime/phase7/collect_phase7a_evidence.py").read_text(encoding="utf-8")
    assert '"docker", "compose", "services", "spark_thrift", "dbt", "metricflow", "dagster"' in text
    assert '"RUNTIME_BOOTSTRAP_VERIFIED"' in text
    assert 'all(evidence["evidence"][key]["status"] == "PASS"' in text


def test_spark_ivy_cache_is_persisted_for_repeatable_workstation_bootstrap():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    volumes = compose["services"]["spark-thrift"]["volumes"]
    assert "spark-ivy-cache:/tmp/.ivy2" in volumes
    assert "spark-ivy-cache" in compose["volumes"]


def test_phase7a_contract_is_yaml_serializable_and_runtime_evidence_schema_is_json_serializable():
    contract = load_contract()
    yaml.safe_dump(contract)
    sample = {
        "contract": "commerce_phase7a_core_runtime_evidence",
        "phase": "7A",
        "runtime_verified": False,
        "status": "INCOMPLETE",
    }
    json.dumps(sample)


def test_phase7a_has_one_canonical_static_runner_and_one_explicit_real_bootstrap_runner():
    closure = load_contract()["closure"]
    assert closure["static_runner"] == "infra/runtime/run_phase7a_static.sh"
    assert closure["real_bootstrap_runner"] == "infra/runtime/run_phase7a_core_bootstrap.sh"
    assert closure["preflight_runner"] == "infra/runtime/run_phase7a_preflight.sh"
    assert closure["runtime_evidence"] == "DEFERRED"
    for key in ("static_runner", "real_bootstrap_runner", "preflight_runner", "doc"):
        assert (ROOT / closure[key]).exists()
