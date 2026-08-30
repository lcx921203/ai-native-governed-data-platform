from __future__ import annotations

import json
import os
from pathlib import Path

from .contracts import ApprovalAuditEvent, ApprovalCase


class ApprovalAuditWriteRefused(PermissionError):
    pass


class JsonlApprovalAuditStore:
    """Append-only local audit sink for approval events.

    This is an engineering adapter, not an immutable production audit database. The event
    hash chain is tamper-evident but is not a cryptographic signature and does not prove
    actor identity. Production should anchor these events in an authenticated audit store.
    """

    def __init__(self, path: Path | str, *, allow_env: str = "PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE"):
        self.path = Path(path)
        self.allow_env = allow_env

    def append_new_case(self, case: ApprovalCase) -> None:
        if len(case.events) != 1:
            raise ValueError("append_new_case expects a case containing exactly the REQUESTED event")
        self._ensure_allowed()
        self._append({"record_type": "APPROVAL_REQUEST", "request": case.request.to_dict(), "event": case.events[0].to_dict()})

    def append_event(self, request_hash: str, event: ApprovalAuditEvent) -> None:
        self._ensure_allowed()
        if event.request_hash != request_hash:
            raise ValueError("event/request hash mismatch")
        self._append({"record_type": "APPROVAL_EVENT", "request_hash": request_hash, "event": event.to_dict()})

    def _ensure_allowed(self) -> None:
        if os.getenv(self.allow_env, "false").lower() != "true":
            raise ApprovalAuditWriteRefused(
                f"Approval audit persistence is disabled; set {self.allow_env}=true only in the intended approval audit service."
            )

    def _append(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
