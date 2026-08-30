from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE_KEYS = ("docker", "compose", "services", "spark_thrift", "dbt", "metricflow", "dagster")


def run(command: list[str], cwd: Path = ROOT) -> dict:
    try:
        process = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
        output = (process.stdout + process.stderr)[-1000:]
        return {
            "status": "PASS" if process.returncode == 0 else "FAIL",
            "returncode": process.returncode,
            "output": output,
        }
    except OSError as exc:
        return {"status": "FAIL", "returncode": 127, "output": str(exc)}


def collect() -> dict:
    evidence = {
        "contract": "commerce_phase7a_core_runtime_evidence",
        "phase": "7A",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": False,
        "status": "INCOMPLETE",
        "evidence": {
            "docker": run(["docker", "info"]),
            "compose": run(["docker", "compose", "version"]),
            "services": run(["docker", "compose", "ps", "--status", "running"]),
            "spark_thrift": run(
                ["docker", "compose", "exec", "-T", "spark-thrift", "/opt/spark/bin/spark-sql", "-e", "SELECT 1"]
            ),
            "dbt": run([str(ROOT / ".venv-dbt/bin/dbt"), "--version"]),
            "metricflow": run(
                [str(ROOT / ".venv-mf/bin/mf"), "health-checks"],
                ROOT / "dbt/mercaso_metricflow_compat",
            ),
            "dagster": run(
                [
                    str(ROOT / ".venv-dagster/bin/dagster"),
                    "definitions",
                    "validate",
                    "-w",
                    "orchestration/dagster/workspace.yaml",
                ]
            ),
        },
    }
    passed = all(evidence["evidence"][key]["status"] == "PASS" for key in CORE_KEYS)
    evidence["runtime_verified"] = passed
    evidence["status"] = "RUNTIME_BOOTSTRAP_VERIFIED" if passed else "INCOMPLETE"
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = collect()
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["runtime_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
