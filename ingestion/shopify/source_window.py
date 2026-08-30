"""Shopify 数据源读取窗口的公共契约。

窗口统一使用半开区间 ``[start, end)``：包含 start，不包含 end。
Dagster 负责逻辑分区；调用方可以在真正读源前把 start 向前扩展 Lookback（回看窗口）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def parse_utc_timestamp(value: str) -> datetime:
    """把 ISO-8601 文本解析成带时区的 UTC datetime。"""

    normalized = value.strip()
    if normalized.endswith("Z"):
        # Python datetime.fromisoformat 更容易识别 +00:00，因此把 Z 规范成 UTC offset。
        normalized = normalized[:-1] + "+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"时间戳必须包含时区: {value}")

    # astimezone(timezone.utc) 把其他时区统一转换成 UTC。
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SourceWindow:
    """有效的源读取区间：start 包含，end 不包含。"""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        # __post_init__ 会在 dataclass 自动 __init__ 完成后执行，适合做参数校验。
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("source window 的 start / end 必须包含时区")
        if self.start >= self.end:
            raise ValueError("window_start 必须早于 window_end")

    @classmethod
    def from_cli_values(
        cls,
        window_start: str | None,
        window_end: str | None,
    ) -> "SourceWindow | None":
        """把命令行字符串参数转换成 SourceWindow；两者都没传时返回 None。"""

        if window_start is None and window_end is None:
            return None
        if window_start is None or window_end is None:
            raise ValueError("--window-start 和 --window-end 必须同时提供")

        # cls(...) 等价于 SourceWindow(...)；classmethod 让子类也能复用这个构造逻辑。
        return cls(
            start=parse_utc_timestamp(window_start),
            end=parse_utc_timestamp(window_end),
        )

    def contains(self, source_updated_at: str) -> bool:
        """判断一条源数据的 updatedAt 是否落在当前半开区间。"""
        value = parse_utc_timestamp(source_updated_at)
        return self.start <= value < self.end
