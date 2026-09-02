"""Append-only JSONL Agent Audit Writer（追加式审计写入器）。

V2：Synchronous Durable Group Commit（同步持久化组提交）。

目标不是“把 Audit 异步丢到后台”：
- 生产默认仍是 Fail Closed；
- ``write()`` 返回前，本条 Record 必须已经进入一次成功的 durable sync；
- 多个并发请求可以共享同一次 ``fdatasync/fsync``；
- 因此降低高并发下“每条记录一次独立 fsync”的放大成本，同时不牺牲 durable ACK。

实现边界：
- 每个 Record 仍然一次 ``os.write``，文件保持 ``O_APPEND``；
- 一个进程 / Audit Path 复用一个文件描述符（FD）；
- 首次创建文件时 fsync Parent Directory，避免只持久化文件数据却丢失目录项；
- 优先 ``fdatasync``，平台不支持时回退 ``fsync``；
- Group Commit Window 很小且受 Policy 上限约束；
- Rotation 必须使用 copytruncate，或在 rename/replace 后重启进程以重新打开 FD；
- 不缓存 Prompt / Answer / Bearer / JWT / Provider Raw Response。

Library 默认 disabled；生产 Runtime 通过环境变量显式启用 jsonl。
"""

from __future__ import annotations

import atexit
import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, RLock
from time import perf_counter, sleep
from typing import Any

import yaml

from .contracts import AgentAuditRecord


class AuditWriteError(RuntimeError):
    """审计写入或 durable sync 失败时的显式错误。"""


@dataclass(frozen=True)
class AuditWriteReceipt:
    """一次 Audit Write 的内部性能收据。

    仅包含数值耗时，不包含 Audit Payload。
    现有调用方可以继续忽略 ``write()`` 返回值。
    """

    append_ms: float
    durability_wait_ms: float
    total_ms: float
    durable: bool
    generation: int


def _durable_sync_fd(fd: int) -> None:
    """优先 fdatasync；不可用平台退回 fsync。"""

    fdatasync = getattr(os, "fdatasync", None)
    if callable(fdatasync):
        fdatasync(fd)
    else:
        os.fsync(fd)


def _fsync_parent_directory(path: Path) -> None:
    """首次创建 Audit File 后持久化 Parent Directory Entry。"""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY

    fd = os.open(path.parent, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class _ProcessDurableAuditSink:
    """一个进程内、一个 Audit Path 对应的共享 Durable Sink。

    并发语义：
    1. 所有 Record 仍通过独立 ``os.write`` 进入 O_APPEND FD；
    2. 第一个需要 durable ACK 的调用者成为 Sync Leader；
    3. Leader 在极小 Coalesce Window 内允许其他线程追加；
    4. Leader 捕获当前 generation 后执行一次 durable sync；
    5. generation <= target 的所有等待者一起收到 ACK；
    6. sync 期间追加的更晚 generation 保守地进入下一轮 sync。

    因此不会把“可能已被同一次 sync 顺便刷下去”的新记录提前标记为 durable。
    """

    def __init__(
        self,
        path: Path,
        *,
        file_mode: int,
        durable_sync: bool,
        group_commit_window_ms: float,
    ):
        self.path = path.resolve()
        self.file_mode = int(file_mode)
        self.durable_sync = bool(durable_sync)
        self.group_commit_window_ms = max(
            0.0,
            float(group_commit_window_ms),
        )

        self._condition = Condition(RLock())
        self._write_generation = 0
        self._durable_generation = 0
        self._sync_in_progress = False
        self._failure: BaseException | None = None
        self._closed = False

        self._sync_count = 0
        self._append_count = 0

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        existed_before_open = self.path.exists()

        flags = (
            os.O_APPEND
            | os.O_CREAT
            | os.O_WRONLY
        )
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        try:
            self._fd = os.open(
                self.path,
                flags,
                self.file_mode,
            )
            os.fchmod(
                self._fd,
                self.file_mode,
            )

            # 文件创建成功但 Parent Directory Entry 未持久化时，
            # 仅对 File FD 做 fdatasync 并不能覆盖掉电后的目录项丢失风险。
            if not existed_before_open:
                _fsync_parent_directory(
                    self.path
                )
        except Exception as exc:
            raise AuditWriteError(
                "Agent audit sink initialization failed."
            ) from exc

    @property
    def append_count(self) -> int:
        """仅供内部测试/诊断读取非敏感计数。"""

        with self._condition:
            return self._append_count

    @property
    def sync_count(self) -> int:
        """仅供内部测试/诊断读取非敏感 durable sync 次数。"""

        with self._condition:
            return self._sync_count

    def _raise_if_unavailable_locked(self) -> None:
        """在持锁状态下检查 Sink 是否仍可使用。"""

        if self._failure is not None:
            raise AuditWriteError(
                "Agent audit durable sink is unavailable."
            ) from self._failure
        if self._closed:
            raise AuditWriteError(
                "Agent audit durable sink is closed."
            )

    def append(
        self,
        payload: bytes,
    ) -> AuditWriteReceipt:
        """Append 一条完整 JSONL，并在返回前等待 durable ACK。"""

        total_started = perf_counter()

        with self._condition:
            self._raise_if_unavailable_locked()

            append_started = perf_counter()
            try:
                written = os.write(
                    self._fd,
                    payload,
                )
            except Exception as exc:
                self._failure = exc
                self._condition.notify_all()
                raise AuditWriteError(
                    "Agent audit append failed."
                ) from exc

            append_ms = max(
                0.0,
                (
                    perf_counter()
                    - append_started
                )
                * 1000,
            )

            if written != len(payload):
                failure = RuntimeError(
                    f"partial append {written}/{len(payload)}"
                )
                self._failure = failure
                self._condition.notify_all()
                raise AuditWriteError(
                    "Agent audit append was partial."
                ) from failure

            self._append_count += 1
            self._write_generation += 1
            generation = (
                self._write_generation
            )

        durability_started = perf_counter()
        if self.durable_sync:
            self._await_durable(
                generation
            )
        durability_wait_ms = max(
            0.0,
            (
                perf_counter()
                - durability_started
            )
            * 1000,
        )

        return AuditWriteReceipt(
            append_ms=append_ms,
            durability_wait_ms=(
                durability_wait_ms
            ),
            total_ms=max(
                0.0,
                (
                    perf_counter()
                    - total_started
                )
                * 1000,
            ),
            durable=self.durable_sync,
            generation=generation,
        )

    def _await_durable(
        self,
        generation: int,
    ) -> None:
        """等待本 generation 被某次成功 durable sync 覆盖。"""

        while True:
            leader = False

            with self._condition:
                self._raise_if_unavailable_locked()

                if (
                    self._durable_generation
                    >= generation
                ):
                    return

                if not self._sync_in_progress:
                    self._sync_in_progress = True
                    leader = True
                else:
                    self._condition.wait()
                    continue

            if not leader:
                continue

            # 不持锁等待：其他线程可以继续 append，随后等待同一轮 ACK。
            if self.group_commit_window_ms > 0:
                sleep(
                    self.group_commit_window_ms
                    / 1000.0
                )

            with self._condition:
                self._raise_if_unavailable_locked()

                # 只承诺 sync 开始前已经观察到的 generation。
                # sync 期间更晚的 append 不会被提前 ACK。
                target_generation = (
                    self._write_generation
                )
                fd = self._fd

            try:
                _durable_sync_fd(
                    fd
                )
            except Exception as exc:
                with self._condition:
                    self._failure = exc
                    self._sync_in_progress = False
                    self._condition.notify_all()
                raise AuditWriteError(
                    "Agent audit durable sync failed."
                ) from exc

            with self._condition:
                self._sync_count += 1
                self._durable_generation = max(
                    self._durable_generation,
                    target_generation,
                )
                self._sync_in_progress = False
                self._condition.notify_all()

    def close(self) -> None:
        """进程退出时关闭共享 FD；正常 Request Path 不主动关闭。"""

        with self._condition:
            if self._closed:
                return

            while self._sync_in_progress:
                self._condition.wait(
                    timeout=0.05
                )

            self._closed = True
            fd = self._fd
            self._condition.notify_all()

        try:
            os.close(fd)
        except OSError:
            pass


_SINKS_LOCK = RLock()
_SINKS: dict[
    tuple[str, int, bool, float],
    _ProcessDurableAuditSink,
] = {}


def _get_process_sink(
    path: Path,
    *,
    file_mode: int,
    durable_sync: bool,
    group_commit_window_ms: float,
) -> _ProcessDurableAuditSink:
    """按受治理配置复用进程级 Audit Sink。"""

    key = (
        str(path.resolve()),
        int(file_mode),
        bool(durable_sync),
        round(
            float(group_commit_window_ms),
            6,
        ),
    )

    with _SINKS_LOCK:
        sink = _SINKS.get(
            key
        )
        if sink is None:
            sink = _ProcessDurableAuditSink(
                path,
                file_mode=file_mode,
                durable_sync=durable_sync,
                group_commit_window_ms=(
                    group_commit_window_ms
                ),
            )
            _SINKS[key] = sink
        return sink


def _close_process_sinks() -> None:
    """解释器退出时回收进程级 Audit FD。"""

    with _SINKS_LOCK:
        sinks = tuple(
            _SINKS.values()
        )
        _SINKS.clear()

    for sink in sinks:
        sink.close()


atexit.register(
    _close_process_sinks
)


class GovernedAuditWriter:
    """根据受治理 Policy 把 Audit Record 追加到本地 JSONL。"""

    def __init__(
        self,
        project_root: Path | str,
    ):
        self.root = Path(
            project_root
        ).resolve()
        self.policy = yaml.safe_load(
            (
                self.root
                / "agent/contracts/agent_audit_policy.yml"
            ).read_text(
                encoding="utf-8"
            )
        )

        env = self.policy[
            "runtime"
        ]
        self.mode = os.getenv(
            str(env["mode_env"]),
            str(env["default_mode"]),
        ).strip().lower()

        allowed = {
            str(item)
            for item in env[
                "allowed_modes"
            ]
        }
        if self.mode not in allowed:
            raise AuditWriteError(
                f"Unsupported audit mode={self.mode!r}; "
                f"allowed={sorted(allowed)}"
            )

        configured_path = os.getenv(
            str(env["path_env"]),
            str(env["default_path"]),
        ).strip()
        path = Path(
            configured_path
        )
        self.path = (
            path
            if path.is_absolute()
            else self.root / path
        ).resolve()

        self.failure_mode = os.getenv(
            str(
                env[
                    "failure_mode_env"
                ]
            ),
            str(
                env[
                    "default_failure_mode"
                ]
            ),
        ).strip().lower()
        if self.failure_mode not in {
            "fail_closed",
            "fail_open",
        }:
            raise AuditWriteError(
                "Unsupported audit failure mode."
            )

        storage = self.policy[
            "storage"
        ]
        self.file_mode = int(
            str(
                storage[
                    "file_mode"
                ]
            ),
            8,
        )
        self.durable_sync = bool(
            storage[
                "acknowledged_record_requires_durable_sync"
            ]
        )

        window_env = str(
            env[
                "group_commit_window_ms_env"
            ]
        )
        default_window = float(
            env[
                "default_group_commit_window_ms"
            ]
        )
        max_window = float(
            env[
                "max_group_commit_window_ms"
            ]
        )

        raw_window = os.getenv(
            window_env,
            str(default_window),
        ).strip()
        try:
            self.group_commit_window_ms = float(
                raw_window
            )
        except ValueError as exc:
            raise AuditWriteError(
                "Audit group commit window must be numeric."
            ) from exc

        if not (
            0.0
            <= self.group_commit_window_ms
            <= max_window
        ):
            raise AuditWriteError(
                "Audit group commit window is outside governed bounds."
            )

        # Sink Lazy Init：Audit disabled 时不创建目录/文件/FD。
        self._sink: (
            _ProcessDurableAuditSink
            | None
        ) = None

    @property
    def enabled(self) -> bool:
        """返回当前进程是否启用持久化审计。"""

        return (
            self.mode
            == "jsonl"
        )

    @property
    def fail_closed(self) -> bool:
        """生产 Audit 失败时是否禁止继续返回业务答案。"""

        return (
            self.enabled
            and self.failure_mode
            == "fail_closed"
        )

    def _get_sink(
        self,
    ) -> _ProcessDurableAuditSink:
        """惰性获取当前进程共享 Sink。"""

        if self._sink is None:
            self._sink = (
                _get_process_sink(
                    self.path,
                    file_mode=self.file_mode,
                    durable_sync=(
                        self.durable_sync
                    ),
                    group_commit_window_ms=(
                        self.group_commit_window_ms
                    ),
                )
            )
        return self._sink

    def write(
        self,
        record: AgentAuditRecord,
    ) -> AuditWriteReceipt | None:
        """写入一条 JSONL；生产默认在 durable ACK 后才返回。

        返回 ``AuditWriteReceipt`` 只用于内部性能验证。
        现有 Runtime / Guard / Timing 调用方无需消费返回值。
        """

        if not self.enabled:
            return None

        try:
            payload = (
                json.dumps(
                    record.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(
                        ",",
                        ":",
                    ),
                )
                + "\n"
            ).encode(
                "utf-8"
            )

            return self._get_sink().append(
                payload
            )
        except AuditWriteError:
            raise
        except Exception as exc:
            raise AuditWriteError(
                "Agent audit persistence failed."
            ) from exc
