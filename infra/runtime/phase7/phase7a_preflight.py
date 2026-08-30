from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "infra/contracts/phase7/runtime_bootstrap.yml"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    blocking: bool = False


def run(command: list[str], timeout: int = 15) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def command_check(name: str, command: str, *, blocking: bool = True) -> Check:
    path = shutil.which(command)
    if path:
        return Check(name, "PASS", path, blocking=False)
    return Check(name, "BLOCKED" if blocking else "WARN", f"command not found: {command}", blocking=blocking)


def check_python(preferred: str) -> Check:
    path = shutil.which(preferred)
    if not path:
        return Check("preferred_python", "BLOCKED", f"{preferred} is required for the pinned workstation runtime", True)
    rc, out = run([preferred, "--version"])
    if rc != 0:
        return Check("preferred_python", "BLOCKED", out or f"failed to execute {preferred}", True)
    return Check("preferred_python", "PASS", out)


def check_docker() -> list[Check]:
    checks: list[Check] = []
    docker = shutil.which("docker")
    if not docker:
        return [Check("docker_cli", "BLOCKED", "docker command not found", True)]
    checks.append(Check("docker_cli", "PASS", docker))
    rc, out = run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if rc != 0:
        checks.append(Check("docker_daemon", "BLOCKED", out or "docker daemon is not reachable", True))
        return checks
    checks.append(Check("docker_daemon", "PASS", f"server={out}"))
    rc, out = run([docker, "compose", "version"], timeout=20)
    if rc != 0:
        checks.append(Check("docker_compose_v2", "BLOCKED", out or "docker compose v2 is unavailable", True))
        return checks
    checks.append(Check("docker_compose_v2", "PASS", out))
    rc, out = run([docker, "compose", "config", "--quiet"], timeout=30)
    if rc != 0:
        checks.append(Check("docker_compose_config", "BLOCKED", out or "docker compose config failed", True))
    else:
        checks.append(Check("docker_compose_config", "PASS", "docker-compose.yml parses successfully"))
    return checks


def port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def check_ports(ports: Iterable[int]) -> list[Check]:
    result = []
    for port in ports:
        if port_is_free(port):
            result.append(Check(f"port_{port}", "PASS", "available"))
        else:
            result.append(Check(f"port_{port}", "BLOCKED", "already in use", True))
    return result


def total_memory_gb() -> float | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return pages * page_size / (1024**3)
    except (ValueError, OSError, AttributeError):
        return None


def resource_checks(contract: dict) -> list[Check]:
    checks: list[Check] = []
    memory = total_memory_gb()
    warning_below = float(contract["host"]["warning_below_memory_gb"])
    if memory is None:
        checks.append(Check("host_memory", "WARN", "unable to determine host memory"))
    elif memory < warning_below:
        checks.append(Check("host_memory", "WARN", f"{memory:.1f} GiB detected; runtime may be resource constrained"))
    else:
        checks.append(Check("host_memory", "PASS", f"{memory:.1f} GiB detected"))

    free_disk = shutil.disk_usage(ROOT).free / (1024**3)
    recommended = float(contract["host"]["recommended_free_disk_gb"])
    if free_disk < recommended:
        checks.append(Check("free_disk", "WARN", f"{free_disk:.1f} GiB free; project recommendation is {recommended:.0f} GiB"))
    else:
        checks.append(Check("free_disk", "PASS", f"{free_disk:.1f} GiB free"))
    return checks


def contract_checks(contract: dict, *, strict: bool) -> list[Check]:
    checks: list[Check] = []
    architecture = platform.machine().lower()
    accepted = {item.lower() for item in contract["host"]["accepted_architectures"]}
    checks.append(
        Check(
            "architecture",
            "PASS" if architecture in accepted else "WARN",
            architecture or "unknown",
        )
    )

    env_path = ROOT / contract["runtime"]["env_file"]
    if env_path.exists():
        checks.append(Check("runtime_env_file", "PASS", str(env_path.relative_to(ROOT))))
    else:
        status = "BLOCKED" if strict else "WARN"
        checks.append(Check("runtime_env_file", status, "copy .env.example to .env before runtime bootstrap", strict))

    required_files = [
        "docker-compose.yml",
        "infra/runtime/VERSIONS.md",
        "infra/runtime/run_pre_dagster_validation.sh",
        "infra/runtime/run_dbt_validation.sh",
        "infra/runtime/run_metricflow_validation.sh",
        "infra/runtime/run_phase3c_dagster_preflight.sh",
        "requirements-dbt.txt",
        "requirements-metricflow-compat.txt",
        "requirements-dagster.txt",
    ]
    for rel in required_files:
        path = ROOT / rel
        checks.append(Check(f"file:{rel}", "PASS" if path.exists() else "BLOCKED", "present" if path.exists() else "missing", not path.exists()))

    bad_shebangs: list[str] = []
    for path in sorted((ROOT / "infra/runtime").glob("*.sh")):
        first = path.read_text(encoding="utf-8").splitlines()[0] if path.stat().st_size else ""
        if not first.startswith("#!"):
            bad_shebangs.append(str(path.relative_to(ROOT)))
    if bad_shebangs:
        checks.append(Check("runtime_shell_shebangs", "BLOCKED", ", ".join(bad_shebangs), True))
    else:
        checks.append(Check("runtime_shell_shebangs", "PASS", "all runtime shell entry points have executable shebangs"))
    return checks


def build_report(*, strict: bool) -> dict:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    checks: list[Check] = []
    checks.extend(contract_checks(contract, strict=strict))
    checks.append(command_check("bash", "bash"))
    checks.append(check_python(contract["host"]["preferred_python"]))
    checks.extend(check_docker())
    ports = [
        *contract["ports"]["core_data_plane"],
        *contract["ports"]["control_plane"],
    ]
    checks.extend(check_ports(ports))
    checks.extend(resource_checks(contract))

    blocked = [check for check in checks if check.blocking and check.status == "BLOCKED"]
    warnings = [check for check in checks if check.status == "WARN"]
    return {
        "contract": contract["contract"],
        "phase": "7A",
        "mode": "STRICT_RUNTIME" if strict else "READINESS_SCAN",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": False,
        "overall": "READY_FOR_BOOTSTRAP" if not blocked else "BLOCKED",
        "blocking_count": len(blocked),
        "warning_count": len(warnings),
        "checks": [asdict(check) for check in checks],
        "note": "Preflight readiness is not runtime evidence. Only successful real service/query acceptance may upgrade observations to RUNTIME_VERIFIED.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="Require .env and all workstation runtime prerequisites.")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()

    report = build_report(strict=args.strict)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json_output:
        output = args.json_output
        if not output.is_absolute():
            output = ROOT / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["overall"] == "READY_FOR_BOOTSTRAP" else 2


if __name__ == "__main__":
    raise SystemExit(main())
