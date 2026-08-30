CREATE NAMESPACE IF NOT EXISTS polaris.raw;

CREATE TABLE IF NOT EXISTS polaris.raw.raw_shopify_order_payload (
    shopify_order_id STRING,
    order_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING,
    source_file STRING,
    payload STRING
)
USING iceberg
PARTITIONED BY (days(order_updated_at));

-- Raw is append-only and is queried primarily by the Shopify source-update window.
ALTER TABLE polaris.raw.raw_shopify_order_payload
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY order_updated_at, shopify_order_id, extracted_at;
