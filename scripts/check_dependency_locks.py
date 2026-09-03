#!/usr/bin/env python3
"""检查依赖锁文件是否覆盖全部环境，并保持 hash-pinned 形式。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

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
LOCK_PACKAGE_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)==",
    re.MULTILINE,
)
REQUIREMENT_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)(?:\[[^]]+\])?\s*(?:[<>=!~]|$)",
)


def lock_path(component: str) -> Path:
    """返回一个组件在 Python 3.11 / Linux 下的规范 lock 路径。"""

    return LOCK_DIR / f"{component}-py311-linux.lock.txt"


def _canonical_name(value: str) -> str:
    """按 Python Package Name 规则归一化大小写、下划线和点。"""

    return re.sub(r"[-_.]+", "-", value).lower()


def declared_requirement_names(
    path: Path,
    *,
    visited: set[Path] | None = None,
) -> set[str]:
    """递归读取 ``-r``，返回一个运行时声明的全部直接依赖名。"""

    resolved = path.resolve()
    seen = set() if visited is None else visited
    if resolved in seen:
        return set()
    seen.add(resolved)

    names: set[str] = set()
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r ") or line.startswith("--requirement "):
            include = line.split(maxsplit=1)[1]
            names.update(
                declared_requirement_names(
                    resolved.parent / include,
                    visited=seen,
                )
            )
            continue
        match = REQUIREMENT_RE.match(line)
        if match:
            names.add(_canonical_name(match.group(1)))
    return names


def validate_lock(
    path: Path,
    requirement_path: Path | None = None,
) -> list[str]:
    """验证 Lock 的 Hash 形式，并证明它覆盖当前顶层依赖声明。"""

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
    if requirement_path is not None:
        declared = declared_requirement_names(requirement_path)
        locked = {
            _canonical_name(name)
            for name in LOCK_PACKAGE_RE.findall(text)
        }
        missing_direct = sorted(declared - locked)
        if missing_direct:
            errors.append(
                f"{path}: missing direct requirements from "
                f"{requirement_path}: {', '.join(missing_direct)}"
            )
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
            requirement_path = ROOT / yaml.safe_load(
                (LOCK_DIR / "LOCK_POLICY.yml").read_text(
                    encoding="utf-8"
                )
            )["components"][component]
            errors.extend(
                validate_lock(
                    path,
                    requirement_path,
                )
            )
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
