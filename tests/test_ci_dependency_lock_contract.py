from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMPONENTS = {
    "agent",
    "dagster",
    "datahub",
    "dbt",
    "mcp",
    "metricflow-compat",
    "rag",
    "serving",
    "streaming",
    "ci",
}


def _load_yaml(path: Path) -> dict:
    """使用 BaseLoader 读取 GitHub Actions，避免 YAML 1.1 把 `on` 当布尔值。"""

    return yaml.load(
        path.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_ci_workflow_has_fast_full_dependency_and_redis_acceptance_jobs():
    """CI 同时保留静态门禁、全量契约、依赖解析和真实 Redis Runtime 验收。"""

    workflow = _load_yaml(
        ROOT / ".github/workflows/ci.yml"
    )
    jobs = workflow["jobs"]

    assert {
        "static-quality",
        "contract-suite",
        "dependency-resolution",
        "redis-runtime-acceptance",
    }.issubset(jobs)

    matrix = jobs[
        "dependency-resolution"
    ]["strategy"]["matrix"]["component"]
    assert set(matrix) == EXPECTED_COMPONENTS

    redis_job = jobs[
        "redis-runtime-acceptance"
    ]
    assert (
        redis_job["services"]["redis"]["image"]
        == "redis:7.4.0-alpine"
    )


def test_redis_acceptance_uses_real_service_hash_locked_client_and_evidence():
    """Real Redis Job 必须启动 Service、从 hash lock 安装 Client，并上传验收证据。"""

    text = (
        ROOT / ".github/workflows/ci.yml"
    ).read_text(encoding="utf-8")

    assert "redis:7.4.0-alpine" in text
    assert 'redis-cli ping' in text
    assert (
        "./scripts/lock_dependencies.sh agent-redis"
        in text
    )
    assert (
        ".ci-locks/agent-redis-py311-linux.lock.txt"
        in text
    )
    assert (
        "tests/runtime/test_agent_redis_runtime_acceptance.py"
        in text
    )
    assert (
        "actions/upload-artifact@v7"
        in text
    )
    assert (
        "redis-runtime-acceptance.xml"
        in text
    )


def test_ci_is_read_only_and_uses_isolated_python_311_runtime():
    workflow = _load_yaml(
        ROOT / ".github/workflows/ci.yml"
    )
    assert (
        workflow["permissions"]["contents"]
        == "read"
    )
    assert (
        workflow["env"]["PYTHON_VERSION"]
        == "3.11"
    )
    assert (
        workflow["env"]["UV_VERSION"]
        == "0.12.1"
    )

    text = (
        ROOT / ".github/workflows/ci.yml"
    ).read_text(encoding="utf-8")
    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text
    assert "astral-sh/setup-uv@v10.0.1" in text

    ci_requirements = (
        ROOT / "requirements-ci.txt"
    ).read_text(encoding="utf-8")
    assert (
        "-r requirements-dagster.txt"
        in ci_requirements
    )
    assert (
        "-r requirements-datahub.txt"
        not in ci_requirements
    )


def test_lock_policy_covers_every_default_runtime_environment():
    """默认 Runtime Lock Policy 保持原来的 10 个环境；Redis Client 仍是可选边界。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "requirements/locks/LOCK_POLICY.yml"
        ).read_text(encoding="utf-8")
    )
    assert (
        policy["python_version"]
        == "3.11"
    )
    assert (
        policy["python_platform"]
        == "x86_64-unknown-linux-gnu"
    )
    assert (
        policy["generate_hashes"]
        is True
    )
    assert (
        set(policy["components"])
        == EXPECTED_COMPONENTS
    )

    for requirement_file in (
        policy["components"].values()
    ):
        assert (
            ROOT / requirement_file
        ).is_file(), requirement_file


def test_lock_script_preserves_runtime_boundaries_and_optional_redis_lock():
    text = (
        ROOT / "scripts/lock_dependencies.sh"
    ).read_text(encoding="utf-8")

    assert "requirements-dbt.txt" in text
    assert (
        "requirements-metricflow-compat.txt"
        in text
    )
    assert (
        'agent-redis) echo "requirements-agent-redis.txt"'
        in text
    )
    assert "--generate-hashes" in text
    assert "--exclude-newer" in text
    assert (
        "x86_64-unknown-linux-gnu"
        in text
    )


def test_dependency_lock_workflow_generates_and_validates_all_default_locks():
    workflow = _load_yaml(
        ROOT
        / ".github/workflows/dependency-locks.yml"
    )
    assert (
        workflow["permissions"]["contents"]
        == "read"
    )

    text = (
        ROOT
        / ".github/workflows/dependency-locks.yml"
    ).read_text(encoding="utf-8")
    assert (
        "./scripts/lock_dependencies.sh all"
        in text
    )
    assert (
        "check_dependency_locks.py --require-all"
        in text
    )
    assert (
        "actions/upload-artifact@v7"
        in text
    )
