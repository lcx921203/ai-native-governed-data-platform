"""Agent Load Test / SLO Calibration Contract 的静态与纯函数测试。"""

from __future__ import annotations

from pathlib import Path

import yaml

from acceptance.agent_slo import build_scenarios, percentile

ROOT = Path(__file__).resolve().parents[1]


def test_percentile_uses_bounded_nearest_rank():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 0.50) == 3.0
    assert percentile(values, 0.95) == 5.0
    assert percentile([], 0.95) is None


def test_ci_smoke_covers_baseline_concurrency_and_rate_saturation():
    scenarios = {item.name: item for item in build_scenarios("ci-smoke")}
    assert set(scenarios) == {
        "baseline",
        "tenant-saturation",
        "global-saturation",
        "subject-rate",
    }
    assert scenarios["baseline"].expect_all_admitted is True
    assert scenarios["tenant-saturation"].expected_rejection_codes == (
        "TENANT_CONCURRENCY_LIMIT",
    )
    assert scenarios["global-saturation"].expected_rejection_codes == (
        "GLOBAL_CONCURRENCY_LIMIT",
    )
    assert scenarios["subject-rate"].expected_rejection_codes == (
        "SUBJECT_RATE_LIMITED",
    )


def test_calibration_policy_refuses_to_promote_ci_latency_to_production_slo():
    policy = yaml.safe_load(
        (ROOT / "agent/contracts/agent_slo_calibration_policy.yml").read_text(
            encoding="utf-8"
        )
    )
    assert policy["status"] == "LAB_EVIDENCE_ONLY"
    assert (
        policy["principles"]["github_hosted_runner_is_not_production_slo_authority"]
        is True
    )
    assert policy["profiles"]["ci_smoke"]["latency_gate"] is False
    assert policy["promotion"]["current_production_slo_status"] == "UNCALIBRATED"
    assert policy["promotion"]["minimum_repeated_runs"] >= 3


def test_runtime_policy_links_to_versioned_calibration_contract():
    policy = yaml.safe_load(
        (ROOT / "agent/contracts/agent_runtime_slo_policy.yml").read_text(
            encoding="utf-8"
        )
    )
    assert (
        policy["calibration"]["policy"]
        == "agent/contracts/agent_slo_calibration_policy.yml"
    )
    assert policy["calibration"]["production_slo_status"] == "UNCALIBRATED"
    assert policy["calibration"]["ci_smoke_is_latency_gate"] is False


def test_load_evidence_source_does_not_serialize_redis_url():
    text = (
        ROOT / "acceptance/agent_slo/redis_load.py"
    ).read_text(encoding="utf-8")
    assert '"redis_endpoint_recorded": False' in text
    assert "Redis URL must never appear in load evidence." in text
    assert '"includes_llm": False' in text
    assert '"production_slo_authority": False' in text
