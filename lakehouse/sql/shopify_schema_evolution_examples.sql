-- Shopify -> Structured Source 的 Schema Evolution 示例 / Runbook Snippets
-- 这些语句不是自动执行脚本。是否执行必须先确认业务语义兼容。

-- 1) 新增可选字段：Iceberg metadata-only schema evolution；旧文件读取时该列为 NULL。
-- ALTER TABLE polaris.source.shopify_order ADD COLUMN customer_locale STRING;

-- 2) 安全 widening 示例：只有确认源字段和下游语义兼容后才执行。
-- ALTER TABLE polaris.source.shopify_order_item ALTER COLUMN quantity TYPE BIGINT;

-- 3) Rename / Drop 不自动化。
-- Iceberg 的 Field ID 能让物理列重命名安全，但“业务语义是否相同”仍需要人工 / Contract Review。

-- 4) String -> Struct、Array -> Object、语义拆分等 Breaking Change：
-- Raw payload STRING 继续保留源证据；Normalize / Structured Source Fail Closed；
-- 修改 GraphQL Query + Parser + Iceberg Schema + dbt Contract 后，再 Backfill 受影响窗口。
