"""面向九张 Shopify Consumer Mart 的 Dagster FreshnessPolicy。

Freshness 描述“消费者最晚什么时候应该拿到该业务日分区”，不是 Schedule 本身。
当前九表集合来自 ``consumer_sla.py``，包含 ``order_lifecycle_snapshot``。
"""

from datetime import timedelta

import dagster as dg

from .automation_policy import (
    SHOPIFY_AUTOMATION_TIMEZONE,
    SHOPIFY_DAILY_FRESHNESS_DEADLINE_CRON,
    SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES,
)
from .consumer_sla import SHOPIFY_DAILY_MART_ASSET_KEYS


SHOPIFY_DAILY_MART_ASSET_KEY_SET = frozenset(
    dg.AssetKey([k]) for k in SHOPIFY_DAILY_MART_ASSET_KEYS
)
SHOPIFY_DAILY_FRESHNESS_POLICY = dg.FreshnessPolicy.cron(
    deadline_cron=SHOPIFY_DAILY_FRESHNESS_DEADLINE_CRON,
    lower_bound_delta=timedelta(minutes=SHOPIFY_DAILY_FRESHNESS_LOWER_BOUND_MINUTES),
    timezone=SHOPIFY_AUTOMATION_TIMEZONE,
)


def apply_shopify_daily_freshness_policy(defs: dg.Definitions) -> dg.Definitions:
    """只给受治理的每日消费者 Mart 挂统一 FreshnessPolicy。
    
    输入完整 ``Definitions``，输出映射后的 ``Definitions``；不在 SLA 集合里的 Source/Staging/Intermediate Asset 保持不变。
    """
    def with_policy(spec: dg.AssetSpec) -> dg.AssetSpec:
        """处理单个 ``AssetSpec``：命中九张受治理 Mart 就附加 FreshnessPolicy，否则原样返回。
        
        ``overwrite_existing=False`` 防止这层公共策略覆盖未来更明确的单表策略。
        """
        if spec.key not in SHOPIFY_DAILY_MART_ASSET_KEY_SET:
            return spec
        return dg.apply_freshness_policy(
            spec,
            SHOPIFY_DAILY_FRESHNESS_POLICY,
            overwrite_existing=False,
        )

    return defs.map_asset_specs(func=with_policy)
