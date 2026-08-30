#!/usr/bin/env python3
"""检查依赖锁文件是否覆盖全部环境，并保持 hash-pinned 形式。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "requirements" / "locks"
COMPONENTS = (
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
)
HASH_RE = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def lock_path(component: str) -> Path:
    """返回一个组件在 Python 3.11 / Linux 下的规范 lock 路径。"""

    return LOCK_DIR / f"{component}-py311-linux.lock.txt"


def validate_lock(path: Path) -> list[str]:
    """验证 lock 不是顶层版本清单，而是包含传递依赖 hash 的解析结果。"""

    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if "uv pip compile" not in text and "lock_dependencies.sh" not in text:
        errors.append(f"{path}: missing generated-header provenance")
    if not HASH_RE.search(text):
        errors.append(f"{path}: no sha256 package hashes found")
    if ">=" in "\n".join(
        line for line in text.splitlines() if line and not line.lstrip().startswith("#")
    ):
        errors.append(f"{path}: unresolved >= constraint remains")
    return errors


def main() -> int:
    """执行锁覆盖检查；`--require-all` 用于正式 CI / 发布门禁。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()

    missing = [str(lock_path(name)) for name in COMPONENTS if not lock_path(name).exists()]
    existing_count = len(COMPONENTS) - len(missing)
    if missing and (args.require_all or existing_count > 0):
        print("Missing dependency locks:")
        print("\n".join(f"- {item}" for item in missing))
        return 1

    errors: list[str] = []
    for component in COMPONENTS:
        path = lock_path(component)
        if path.exists():
            errors.extend(validate_lock(path))
    if errors:
        print("Dependency lock validation failed:")
        print("\n".join(f"- {item}" for item in errors))
        return 1

    print(f"Validated {existing_count} dependency lock(s).")
    if missing:
        print("Full online lock generation is still required for:", ", ".join(Path(p).name for p in missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
