from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def require_gate(name: str) -> None:
    if os.getenv(name, "false").lower() != "true":
        raise SystemExit(f"REFUSED: set {name}=true explicitly.")


def require_verified_evidence(relative_path: str, expected_status: str) -> dict[str, Any]:
    path = ROOT / relative_path
    if not path.exists():
        raise SystemExit(f"DEFERRED: required runtime evidence is missing: {relative_path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"DEFERRED: runtime evidence is unreadable: {relative_path}: {exc}") from exc
    if payload.get("runtime_verified") is not True or payload.get("status") != expected_status:
        raise SystemExit(
            f"DEFERRED: runtime evidence is not verified: {relative_path}; "
            f"expected status={expected_status!r}, observed={payload.get('status')!r}"
        )
    return payload


def write_verified_evidence(relative_path: str, *, status: str, details: dict[str, Any]) -> Path:
    output = ROOT / relative_path
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime_verified": True,
        "status": status,
        **details,
    }
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output
