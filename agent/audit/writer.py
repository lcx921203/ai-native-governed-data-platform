"""Append-only JSONL Agent Audit Writer（追加式审计写入器）。

V1 使用单行 JSON + O_APPEND：
- 每个 Record 一次 os.write；
- 文件权限默认为 0600；
- 可选 fsync，生产策略默认开启；
- 不做日志轮转，轮转属于部署层（logrotate / sidecar / collector）职责。

Library 默认 disabled，生产 `scripts/run_agent_api.sh` 会默认打开 jsonl。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from .contracts import AgentAuditRecord


class AuditWriteError(RuntimeError):
    """审计写入失败时的显式错误。"""


class GovernedAuditWriter:
    """根据受治理 Policy 把 Audit Record 追加到本地 JSONL。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/agent_audit_policy.yml").read_text(
                encoding="utf-8"
            )
        )

        env = self.policy["runtime"]
        self.mode = os.getenv(
            str(env["mode_env"]),
            str(env["default_mode"]),
        ).strip().lower()

        allowed = {str(x) for x in env["allowed_modes"]}
        if self.mode not in allowed:
            raise AuditWriteError(
                f"Unsupported audit mode={self.mode!r}; allowed={sorted(allowed)}"
            )

        configured_path = os.getenv(
            str(env["path_env"]),
            str(env["default_path"]),
        ).strip()
        path = Path(configured_path)
        self.path = path if path.is_absolute() else (self.root / path)
        self.path = self.path.resolve()

        self.failure_mode = os.getenv(
            str(env["failure_mode_env"]),
            str(env["default_failure_mode"]),
        ).strip().lower()
        if self.failure_mode not in {"fail_closed", "fail_open"}:
            raise AuditWriteError(
                f"Unsupported audit failure mode={self.failure_mode!r}"
            )

        self.fsync = bool(self.policy["storage"]["fsync_each_record"])

    @property
    def enabled(self) -> bool:
        """返回当前进程是否启用持久化审计。"""

        return self.mode == "jsonl"

    @property
    def fail_closed(self) -> bool:
        """生产 Audit 失败时是否禁止继续返回业务答案。"""

        return self.enabled and self.failure_mode == "fail_closed"

    def write(self, record: AgentAuditRecord) -> None:
        """以一次 append syscall 写入一条 JSONL Record。"""

        if not self.enabled:
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                record.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
        try:
            fd = os.open(self.path, flags, 0o600)
            try:
                written = os.write(fd, payload)
                if written != len(payload):
                    raise AuditWriteError(
                        f"Partial audit append: {written}/{len(payload)} bytes"
                    )
                if self.fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)
        except AuditWriteError:
            raise
        except Exception as exc:
            raise AuditWriteError(
                "Agent audit persistence failed."
            ) from exc
