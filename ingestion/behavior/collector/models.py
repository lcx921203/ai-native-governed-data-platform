"""行为埋点 HTTP 请求模型。

Collector 只做轻量入口治理：结构校验、接收时间、请求 ID，然后写 Kafka。
复杂的事件时间、去重、窗口与迟到数据处理留给 Flink。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BehaviorEventIn(BaseModel):
    """浏览器 / App SDK 上报的一条业务行为事件。"""

    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=128)
    event_name: str = Field(min_length=1, max_length=128)
    event_time: datetime
    user_id: str | None = None
    session_id: str | None = None
    item_id: str | None = None
    store_id: str | None = None
    page_url: str | None = None
    device_type: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1

    @field_validator("event_time")
    @classmethod
    def event_time_must_have_timezone(cls, value: datetime) -> datetime:
        """拒绝没有时区的时间，避免不同机器把本地时间解释成不同 UTC。"""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("event_time 必须带时区，例如 2026-08-20T09:10:21Z")
        return value
