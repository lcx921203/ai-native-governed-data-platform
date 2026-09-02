"""Authenticated Agent API E2E Load Framework 的确定性契约测试。"""

from __future__ import annotations

from pathlib import Path

import yaml

from acceptance.agent_slo.api_e2e_load import build_api_e2e_scenarios


ROOT = Path(__file__).resolve().parents[1]


def test_api_e2e_ci_profile_covers_success_concurrency_and_rate_paths():
    """CI Profile 必须覆盖 200、Tenant Concurrency 429 与 Subject RPM 429。"""

    scenarios = {
        item.name: item
        for item in build_api_e2e_scenarios(
            "ci-smoke"
        )
    }
    assert set(scenarios) == {
        "authenticated-baseline",
        "tenant-concurrency",
        "subject-rate",
    }
    assert (
        scenarios[
            "authenticated-baseline"
        ].expect_all_200
        is True
    )
    assert (
        scenarios[
            "tenant-concurrency"
        ].expected_429_code
        == "TENANT_CONCURRENCY_LIMIT"
    )
    assert (
        scenarios[
            "subject-rate"
        ].expected_429_code
        == "SUBJECT_RATE_LIMITED"
    )


def test_api_e2e_source_uses_real_http_jwt_redis_and_governed_runtime_boundary():
    """Source 必须启动 Uvicorn、真实 RS256 JWT/JWKS，并走 Production Agent API。"""

    text = (
        ROOT
        / "acceptance/agent_slo/api_e2e_load.py"
    ).read_text(encoding="utf-8")

    assert '"-m",' in text
    assert '"uvicorn"' in text
    assert '"/api/v1/agent/query"' in text
    assert '"Authorization": f"Bearer {token}"' in text
    assert '"RS256"' in text
    assert "ThreadingHTTPServer" in text
    assert '"AGENT_API_TRAFFIC_BACKEND": "redis"' in text
    assert '"AGENT_RENDERER_MODE": "deterministic"' in text
    assert 'env.pop("OPENAI_API_KEY", None)' in text


def test_api_e2e_evidence_is_explicitly_not_production_slo_authority():
    """Loopback E2E Evidence 仍不能跳过 Staging/Live Dependency 标定。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/agent_slo_calibration_policy.yml"
        ).read_text(encoding="utf-8")
    )

    assert policy["version"] == 2
    assert (
        policy["principles"][
            "authenticated_api_e2e_benchmark_is_not_production_slo"
        ]
        is True
    )
    assert (
        policy["profiles"][
            "api_e2e_ci_smoke"
        ]["production_slo_authority"]
        is False
    )
    assert (
        policy["profiles"][
            "api_e2e_ci_smoke"
        ]["live_llm"]
        is False
    )
    assert (
        policy["promotion"][
            "current_production_slo_status"
        ]
        == "UNCALIBRATED"
    )


def test_ci_and_manual_lab_publish_agent_api_e2e_evidence():
    """Push CI 跑小 Smoke；手动 Workflow 跑较大 Lab，二者都上传 JSON Evidence。"""

    ci = (
        ROOT
        / ".github/workflows/ci.yml"
    ).read_text(encoding="utf-8")
    lab = (
        ROOT
        / ".github/workflows/agent-slo-calibration.yml"
    ).read_text(encoding="utf-8")

    assert (
        "Run authenticated Agent API E2E load smoke"
        in ci
    )
    assert (
        "agent-api-e2e-load-smoke.json"
        in ci
    )
    assert (
        "--profile ci-smoke"
        in ci
    )

    assert (
        "Run authenticated Agent API E2E load lab"
        in lab
    )
    assert (
        "agent-api-e2e-load-lab.json"
        in lab
    )
    assert (
        "--profile lab"
        in lab
    )


def test_api_e2e_report_source_forbids_prompt_and_runtime_endpoints():
    """Evidence 不能保存 Prompt、Redis URL 或临时 JWKS Endpoint。"""

    text = (
        ROOT
        / "acceptance/agent_slo/api_e2e_load.py"
    ).read_text(encoding="utf-8")

    assert '"redis_endpoint_recorded": False' in text
    assert '"jwks_endpoint_recorded": False' in text
    assert "QUESTION," in text
    assert (
        "Secret/runtime endpoint/prompt must never appear"
        in text
    )
    assert '"includes_live_llm": False' in text
    assert '"includes_live_metricflow": False' in text
    assert '"includes_live_trino": False' in text
