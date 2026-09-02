"""Append-only JSONL Agent Audit Writer（追加式审计写入器）。

V3：Durable Group Commit + Internal Persistence Breakdown（持久化组提交 + 内部耗时拆解）。

生产安全边界保持不变：
- ``write()`` 返回前，本条 Record 必须已经被一次成功 durable sync 覆盖；
- 多个并发请求允许共享同一次 ``fdatasync/fsync``；
- durable sync 失败后 Sink 进入不可用状态，Fail-Closed 调用方不能继续返回答案。

V3 只增加数值型内部 Receipt：
- serialize_ms：JSON 序列化；
- append_lock_wait_ms：等待共享 Append Lock；
- append_ms：单次 ``os.write``；
- durability_wait_ms：从 Append 后到 Durable ACK 的每请求等待；
- batch_coalesce_ms：该 Durable Batch 的实际聚合窗口；
- batch_sync_ms：该 Durable Batch 的真实 fdatasync/fsync；
- batch_*_records：该 Batch 内不同 Event Type 数量。

这些字段不包含 Prompt / Answer / Bearer / JWT / Provider Raw Response。
"""

from __future__ import annotations

import atexit
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, RLock
from time import perf_counter, sleep

import yaml

from .contracts import AgentAuditRecord


class AuditWriteError(RuntimeError):
    """审计写入或 durable sync 失败时的显式错误。"""


@dataclass(frozen=True)
class _DurableBatchReceipt:
    """一次共享 Durable Sync 的内部批次结果。"""

    batch_id: int
    total_records: int
    runtime_records: int
    api_guard_records: int
    api_timing_records: int
    other_records: int
    coalesce_ms: float
    sync_ms: float


@dataclass(frozen=True)
class AuditWriteReceipt:
    """一次 Audit Write 的内部数值收据。

    现有调用方可以继续忽略返回值；只有内部性能诊断会读取这些字段。
    """

    serialize_ms: float
    append_lock_wait_ms: float
    append_ms: float
    durability_wait_ms: float
    writer_residual_ms: float
    total_ms: float

    durable: bool
    generation: int

    batch_id: int
    batch_total_records: int
    batch_runtime_records: int
    batch_api_guard_records: int
    batch_api_timing_records: int
    batch_other_records: int
    batch_coalesce_ms: float
    batch_sync_ms: float


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

    Group Commit 语义：
    1. 每条 Record 仍单独 ``os.write`` 到 ``O_APPEND`` FD；
    2. 第一个等待 Durable ACK 的线程成为 Sync Leader；
    3. Leader 在很小的 Coalesce Window 内允许其他线程继续 Append；
    4. 一个 durable sync 可以一次确认多个 generation；
    5. 每个 generation 都拿到它所属 Batch 的同一份数值元数据；
    6. sync 期间才 Append 的更晚 generation 保守进入下一 Batch。
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

        self._generation_event_types: dict[int, str] = {}
        self._batch_receipts: dict[int, _DurableBatchReceipt] = {}

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
        *,
        event_type: str = "RUNTIME",
    ) -> dict[str, object]:
        """Append 一条 JSONL，并返回 Sink 内部数值耗时。"""

        total_started = perf_counter()
        append_lock_started = perf_counter()

        with self._condition:
            append_lock_wait_ms = max(
                0.0,
                (
                    perf_counter()
                    - append_lock_started
                )
                * 1000,
            )
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
            generation = self._write_generation
            self._generation_event_types[generation] = (
                str(event_type or "RUNTIME")
            )

        durability_started = perf_counter()

        if self.durable_sync:
            batch = self._await_durable(
                generation
            )
        else:
            batch = _DurableBatchReceipt(
                batch_id=0,
                total_records=1,
                runtime_records=(
                    1 if event_type == "RUNTIME" else 0
                ),
                api_guard_records=(
                    1 if event_type == "API_GUARD" else 0
                ),
                api_timing_records=(
                    1 if event_type == "API_TIMING" else 0
                ),
                other_records=(
                    0
                    if event_type in {
                        "RUNTIME",
                        "API_GUARD",
                        "API_TIMING",
                    }
                    else 1
                ),
                coalesce_ms=0.0,
                sync_ms=0.0,
            )

        durability_wait_ms = max(
            0.0,
            (
                perf_counter()
                - durability_started
            )
            * 1000,
        )

        return {
            "append_lock_wait_ms": append_lock_wait_ms,
            "append_ms": append_ms,
            "durability_wait_ms": durability_wait_ms,
            "sink_total_ms": max(
                0.0,
                (
                    perf_counter()
                    - total_started
                )
                * 1000,
            ),
            "generation": generation,
            "batch": batch,
        }

    def _await_durable(
        self,
        generation: int,
    ) -> _DurableBatchReceipt:
        """等待本 generation 被成功 Durable Batch 覆盖，并返回该 Batch 元数据。"""

        while True:
            leader = False

            with self._condition:
                self._raise_if_unavailable_locked()

                if (
                    self._durable_generation
                    >= generation
                ):
                    receipt = self._batch_receipts.pop(
                        generation,
                        None,
                    )
                    if receipt is None:
                        failure = RuntimeError(
                            "missing durable batch receipt"
                        )
                        self._failure = failure
                        self._condition.notify_all()
                        raise AuditWriteError(
                            "Agent audit durable receipt is unavailable."
                        ) from failure
                    return receipt

                if not self._sync_in_progress:
                    self._sync_in_progress = True
                    leader = True
                else:
                    self._condition.wait()
                    continue

            if not leader:
                continue

            coalesce_started = perf_counter()
            if self.group_commit_window_ms > 0:
                sleep(
                    self.group_commit_window_ms
                    / 1000.0
                )
            coalesce_ms = max(
                0.0,
                (
                    perf_counter()
                    - coalesce_started
                )
                * 1000,
            )

            with self._condition:
                self._raise_if_unavailable_locked()

                previous_durable = (
                    self._durable_generation
                )
                target_generation = (
                    self._write_generation
                )
                fd = self._fd

                event_types = [
                    self._generation_event_types.get(
                        item,
                        "OTHER",
                    )
                    for item in range(
                        previous_durable + 1,
                        target_generation + 1,
                    )
                ]

            sync_started = perf_counter()
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

            sync_ms = max(
                0.0,
                (
                    perf_counter()
                    - sync_started
                )
                * 1000,
            )

            with self._condition:
                self._sync_count += 1
                batch_id = self._sync_count

                counts = Counter(event_types)
                known_count = (
                    counts.get("RUNTIME", 0)
                    + counts.get("API_GUARD", 0)
                    + counts.get("API_TIMING", 0)
                )
                total_records = len(event_types)

                batch = _DurableBatchReceipt(
                    batch_id=batch_id,
                    total_records=total_records,
                    runtime_records=counts.get(
                        "RUNTIME",
                        0,
                    ),
                    api_guard_records=counts.get(
                        "API_GUARD",
                        0,
                    ),
                    api_timing_records=counts.get(
                        "API_TIMING",
                        0,
                    ),
                    other_records=max(
                        0,
                        total_records - known_count,
                    ),
                    coalesce_ms=coalesce_ms,
                    sync_ms=sync_ms,
                )

                for item in range(
                    previous_durable + 1,
                    target_generation + 1,
                ):
                    self._batch_receipts[
                        item
                    ] = batch
                    self._generation_event_types.pop(
                        item,
                        None,
                    )

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
        """写入一条 JSONL；生产默认在 Durable ACK 后才返回。"""

        if not self.enabled:
            return None

        total_started = perf_counter()

        try:
            serialize_started = perf_counter()
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
            serialize_ms = max(
                0.0,
                (
                    perf_counter()
                    - serialize_started
                )
                * 1000,
            )

            sink_result = self._get_sink().append(
                payload,
                event_type=str(
                    record.event_type
                    or "RUNTIME"
                ),
            )
            batch = sink_result["batch"]
            if not isinstance(
                batch,
                _DurableBatchReceipt,
            ):
                raise AuditWriteError(
                    "Agent audit batch receipt is invalid."
                )

            total_ms = max(
                0.0,
                (
                    perf_counter()
                    - total_started
                )
                * 1000,
            )
            append_lock_wait_ms = float(
                sink_result[
                    "append_lock_wait_ms"
                ]
            )
            append_ms = float(
                sink_result[
                    "append_ms"
                ]
            )
            durability_wait_ms = float(
                sink_result[
                    "durability_wait_ms"
                ]
            )

            writer_residual_ms = max(
                0.0,
                total_ms
                - serialize_ms
                - append_lock_wait_ms
                - append_ms
                - durability_wait_ms,
            )

            return AuditWriteReceipt(
                serialize_ms=serialize_ms,
                append_lock_wait_ms=(
                    append_lock_wait_ms
                ),
                append_ms=append_ms,
                durability_wait_ms=(
                    durability_wait_ms
                ),
                writer_residual_ms=(
                    writer_residual_ms
                ),
                total_ms=total_ms,
                durable=self.durable_sync,
                generation=int(
                    sink_result[
                        "generation"
                    ]
                ),
                batch_id=batch.batch_id,
                batch_total_records=(
                    batch.total_records
                ),
                batch_runtime_records=(
                    batch.runtime_records
                ),
                batch_api_guard_records=(
                    batch.api_guard_records
                ),
                batch_api_timing_records=(
                    batch.api_timing_records
                ),
                batch_other_records=(
                    batch.other_records
                ),
                batch_coalesce_ms=(
                    batch.coalesce_ms
                ),
                batch_sync_ms=(
                    batch.sync_ms
                ),
            )
        except AuditWriteError:
            raise
        except Exception as exc:
            raise AuditWriteError(
                "Agent audit persistence failed."
            ) from exc
