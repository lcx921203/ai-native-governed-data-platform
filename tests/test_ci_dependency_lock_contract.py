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

    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_ci_workflow_has_fast_full_and_dependency_resolution_jobs():
    workflow = _load_yaml(ROOT / ".github/workflows/ci.yml")
    jobs = workflow["jobs"]
    assert {"static-quality", "contract-suite", "dependency-resolution"}.issubset(jobs)

    matrix = jobs["dependency-resolution"]["strategy"]["matrix"]["component"]
    assert set(matrix) == EXPECTED_COMPONENTS


def test_ci_is_read_only_and_uses_isolated_python_311_runtime():
    workflow = _load_yaml(ROOT / ".github/workflows/ci.yml")
    assert workflow["permissions"]["contents"] == "read"
    assert workflow["env"]["PYTHON_VERSION"] == "3.11"
    assert workflow["env"]["UV_VERSION"] == "0.12.1"

    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "actions/checkout@v7" in text
    assert "actions/setup-python@v7" in text
    assert "astral-sh/setup-uv@v10.0.1" in text


def test_lock_policy_covers_every_runtime_environment():
    policy = yaml.safe_load((ROOT / "requirements/locks/LOCK_POLICY.yml").read_text(encoding="utf-8"))
    assert policy["python_version"] == "3.11"
    assert policy["python_platform"] == "x86_64-unknown-linux-gnu"
    assert policy["generate_hashes"] is True
    assert set(policy["components"]) == EXPECTED_COMPONENTS

    for requirement_file in policy["components"].values():
        assert (ROOT / requirement_file).is_file(), requirement_file


def test_lock_script_preserves_metricflow_dbt_environment_boundary():
    text = (ROOT / "scripts/lock_dependencies.sh").read_text(encoding="utf-8")
    assert "requirements-dbt.txt" in text
    assert "requirements-metricflow-compat.txt" in text
    assert "--generate-hashes" in text
    assert "--exclude-newer" in text
    assert "x86_64-unknown-linux-gnu" in text


def test_dependency_lock_workflow_generates_and_validates_all_locks():
    workflow = _load_yaml(ROOT / ".github/workflows/dependency-locks.yml")
    assert workflow["permissions"]["contents"] == "read"
    text = (ROOT / ".github/workflows/dependency-locks.yml").read_text(encoding="utf-8")
    assert "./scripts/lock_dependencies.sh all" in text
    assert "check_dependency_locks.py --require-all" in text
    assert "actions/upload-artifact@v7" in text
