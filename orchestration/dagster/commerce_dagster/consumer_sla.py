"""当前受治理 Consumer Mart 的 SLA 注册表。

历史 Phase 6 ZIP / SHA-256 保留八张 Mart 的当时闭包；当前 canonical source 仍可正常演进。
2026-08-20 新增 ``order_lifecycle_snapshot`` 后，当前工程通过这个独立注册表把
消费者 Freshness / Exact Partition Completeness / Recovery SLA 扩展为九张 Mart。
选择独立注册表是为了让历史八表语义与当前九表语义都清楚可追溯，不是因为 current source 不能修改。

这只是 Source / Static Contract；真实 Dagster Daemon、Spark/Iceberg 九表完成证据仍然 DEFERRED。
"""

SHOPIFY_DAILY_MART_ASSET_KEYS = (
    "orders",
    "order_lifecycle_snapshot",
    "order_items",
    "payment_transactions",
    "refunds",
    "refund_items",
    "fulfillments",
    "fulfillment_items",
    "fulfillment_events",
)

SHOPIFY_DAILY_MART_COUNT = len(SHOPIFY_DAILY_MART_ASSET_KEYS)
