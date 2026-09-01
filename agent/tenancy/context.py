"""RequestContext 的异步安全绑定。

ContextVar 可以在单进程并发 / asyncio 场景中隔离每次请求，
避免使用全局 mutable tenant state。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from .contracts import RequestContext


_CURRENT_REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar(
    "governed_agent_request_context",
    default=None,
)


def current_request_context() -> RequestContext | None:
    return _CURRENT_REQUEST_CONTEXT.get()


@contextmanager
def bind_request_context(context: RequestContext | None) -> Iterator[None]:
    token = _CURRENT_REQUEST_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_REQUEST_CONTEXT.reset(token)
