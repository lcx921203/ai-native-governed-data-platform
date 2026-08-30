"""Serving API 的 Trino 只读连接配置。

业务逻辑：API 只读取 ``iceberg.serving`` 稳定消费表；不获得 MetricFlow 定义权或 Lakehouse 写权限。
所有连接参数来自环境变量，源码不保存凭据。
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ServingApiSettings:
    """FastAPI → Trino 的最小只读连接配置。

    Catalog/Schema 必须是简单 Identifier；凭据不写入源码。Demo 默认 HTTP，生产可通过环境切 HTTPS。
    """

    host: str = "localhost"
    port: int = 8088
    user: str = "serving_api"
    catalog: str = "iceberg"
    schema: str = "serving"
    http_scheme: str = "http"

    @classmethod
    def from_env(cls) -> "ServingApiSettings":
        """从环境构建配置并做 Identifier / Scheme 白名单校验。

        工程边界：这里只控制连接位置，不允许环境变量把 API 指向任意 Table 或重写 Metric Contract。
        """
        settings = cls(
            host=os.getenv("TRINO_HOST", "localhost"),
            port=int(os.getenv("TRINO_PORT", "8088")),
            user=os.getenv("TRINO_USER", "serving_api"),
            catalog=os.getenv("TRINO_CATALOG", "iceberg"),
            schema=os.getenv("TRINO_SCHEMA", "serving"),
            http_scheme=os.getenv("TRINO_HTTP_SCHEME", "http"),
        )
        for label, value in (("catalog", settings.catalog), ("schema", settings.schema)):
            if not _IDENTIFIER.fullmatch(value):
                raise ValueError(f"invalid Trino {label} identifier: {value!r}")
        if settings.http_scheme not in {"http", "https"}:
            raise ValueError("TRINO_HTTP_SCHEME must be http or https")
        return settings
