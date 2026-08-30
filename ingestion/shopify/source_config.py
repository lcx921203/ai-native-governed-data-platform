"""Shopify Raw 采集的数据源模式配置。

这个项目保留两条输入路径，但不额外维护一份数据源切换配置文件：

- ``fixture``：本地模拟数据，适合 Demo、Clean-room 和没有真实 Token 的环境。
- ``production``：真实 Shopify Admin GraphQL API。

源码默认走 ``fixture``。生产部署时只需要显式设置：

    SHOPIFY_SOURCE_MODE=production

Shop Domain、Access Token 等密钥仍然只放环境变量，不写进源码。

学习时可以把这个文件理解成三步：
1. 用 ``os.getenv`` 读取环境变量；
2. 把字符串转换成 int / float / bool 等 Python 类型；
3. 返回一个统一的配置对象，交给 Dagster Raw Asset 决定走哪条采集路径。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


DEFAULT_SOURCE_MODE = "fixture"
SUPPORTED_SOURCE_MODES = {"fixture", "production"}


@dataclass(frozen=True)
class ShopifySourceConfig:
    """一次 Raw 物化最终使用的数据源配置。

    Python 语法：``@dataclass`` 会自动生成 ``__init__`` 等基础方法；
    ``frozen=True`` 表示对象创建后不允许再修改字段，避免运行中被意外改写。
    """

    mode: str
    kind: str
    values: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        """读取一个可选配置；没有该 key 时返回 default。

        Python 语法：``dict.get(key, default)`` 不会因为 key 不存在而抛 KeyError。
        """
        return self.values.get(key, default)


def _env_int(name: str, default: int) -> int:
    """把环境变量读取为整数；未配置时使用默认值。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        # 工程边界：配置写错时直接失败，不把错误值静默吞掉。
        raise ValueError(f"{name} 必须是整数，当前值为 {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    """把环境变量读取为浮点数。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字，当前值为 {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    """把常见的 true / false 字符串转换成 Python bool。"""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default

    # Python 语法：strip() 去两端空格；lower() 转成小写，方便统一比较。
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(
        f"{name} 必须是 true/false/1/0/yes/no/on/off 之一，当前值为 {raw!r}"
    )


def load_source_config() -> ShopifySourceConfig:
    """根据 ``SHOPIFY_SOURCE_MODE`` 选择 Fixture 或 Production。

    工程规则：
    - 没配置：安全默认 fixture；
    - production：走真实 Shopify API；
    - 其他拼写：Fail Closed（失败关闭），直接报错。

    这样可以避免生产环境把 ``production`` 写错后，悄悄退回模拟数据。
    """

    mode = os.getenv("SHOPIFY_SOURCE_MODE", DEFAULT_SOURCE_MODE).strip().lower()

    if mode not in SUPPORTED_SOURCE_MODES:
        raise ValueError(
            f"不支持 SHOPIFY_SOURCE_MODE={mode!r}；"
            f"只允许 {sorted(SUPPORTED_SOURCE_MODES)}"
        )

    if mode == "fixture":
        return ShopifySourceConfig(
            mode="fixture",
            kind="fixture",
            values={
                "fixture_dir": os.getenv(
                    "SHOPIFY_FIXTURE_DIR",
                    "data/fixtures/shopify",
                ),
            },
        )

    # Production 分支这里只读取非密钥参数。
    # 真正的 SHOPIFY_SHOP_DOMAIN / SHOPIFY_ADMIN_ACCESS_TOKEN
    # 由发 HTTP 请求的 extract_orders.py 在需要时读取。
    return ShopifySourceConfig(
        mode="production",
        kind="shopify_admin_graphql",
        values={
            "api_version": os.getenv("SHOPIFY_API_VERSION", "2026-07"),
            "page_size": _env_int("SHOPIFY_PAGE_SIZE", 100),
            "nested_page_size": _env_int("SHOPIFY_NESTED_PAGE_SIZE", 100),
            "timeout_seconds": _env_int("SHOPIFY_API_TIMEOUT_SECONDS", 60),
            "max_retries": _env_int("SHOPIFY_API_MAX_RETRIES", 5),
            "backoff_base_seconds": _env_float(
                "SHOPIFY_API_BACKOFF_BASE_SECONDS",
                1.0,
            ),
            # 主动限流保留一点 Query Cost 余量，避免每次把 throttle bucket 打到 0。
            "throttle_reserve_points": _env_float(
                "SHOPIFY_THROTTLE_RESERVE_POINTS",
                20.0,
            ),
            # API 版本不一致说明可能发生 fall-forward / schema 风险，默认失败关闭。
            "enforce_api_version": _env_bool(
                "SHOPIFY_ENFORCE_API_VERSION",
                True,
            ),
            "landing_dir": os.getenv(
                "SHOPIFY_API_LANDING_DIR",
                ".runtime/shopify-api",
            ),
            "nested_pagination": _env_bool(
                "SHOPIFY_NESTED_PAGINATION",
                True,
            ),
        },
    )
