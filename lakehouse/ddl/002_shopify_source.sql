CREATE NAMESPACE IF NOT EXISTS polaris.source;

-- Structured Source 的共同思想：
--   Raw              = API Observation
--   Structured Source = Distinct Business Content Version
--
-- 同一个业务对象允许多个不同 record_hash 版本存在。

CREATE TABLE IF NOT EXISTS polaris.source.shopify_order (
    order_id STRING,
    order_name STRING,
    store_id STRING,

    created_at TIMESTAMP,
    processed_at TIMESTAMP,
    updated_at TIMESTAMP,
    cancelled_at TIMESTAMP,
    closed_at TIMESTAMP,

    financial_status STRING,
    fulfillment_status STRING,

    currency_code STRING,

    original_total_amount DECIMAL(18, 2),
    current_total_amount DECIMAL(18, 2),
    current_total_discount_amount DECIMAL(18, 2),
    total_refunded_amount DECIMAL(18, 2),

    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_order_item (
    line_item_id STRING,
    order_id STRING,
    item_id STRING,
    variant_id STRING,
    sku STRING,
    item_title STRING,

    quantity BIGINT,
    current_quantity BIGINT,
    refundable_quantity BIGINT,
    unfulfilled_quantity BIGINT,

    original_unit_price DECIMAL(18, 2),
    original_total_amount DECIMAL(18, 2),

    -- 只保留 Shopify 源字段语义，不把它当成完整业务折扣。
    source_line_discount_amount DECIMAL(18, 2),

    currency_code STRING,
    order_updated_at TIMESTAMP,

    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_line_item_discount_allocation (
    order_id STRING,
    line_item_id STRING,
    discount_application_index INT,
    discount_application_type STRING,
    allocation_method STRING,
    target_selection STRING,
    target_type STRING,
    allocated_amount DECIMAL(18, 2),
    currency_code STRING,
    order_updated_at TIMESTAMP,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_transaction (
    transaction_id STRING,
    order_id STRING,
    parent_transaction_id STRING,
    kind STRING,
    status STRING,
    gateway STRING,
    created_at TIMESTAMP,
    processed_at TIMESTAMP,
    amount DECIMAL(18, 2),
    currency_code STRING,
    is_test BOOLEAN,
    error_code STRING,
    order_updated_at TIMESTAMP,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_refund (
    refund_id STRING,
    order_id STRING,
    created_at TIMESTAMP,
    processed_at TIMESTAMP,
    updated_at TIMESTAMP,
    total_refunded_amount DECIMAL(18, 2),
    currency_code STRING,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_refund_item (
    refund_line_item_id STRING,
    refund_id STRING,
    order_id STRING,
    line_item_id STRING,
    quantity BIGINT,
    restocked BOOLEAN,
    restock_type STRING,
    subtotal_amount DECIMAL(18, 2),
    tax_amount DECIMAL(18, 2),
    currency_code STRING,
    refund_updated_at TIMESTAMP,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


-- Refund 与实际资金退款 Transaction 的关联桥。
-- 该表本身不承载金额指标，用于业务退款记录与资金交易对账。
CREATE TABLE IF NOT EXISTS polaris.source.shopify_refund_transaction (
    refund_id STRING,
    order_id STRING,
    transaction_id STRING,
    refund_updated_at TIMESTAMP,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_fulfillment (
    fulfillment_id STRING,
    order_id STRING,
    fulfillment_name STRING,
    fulfillment_status STRING,
    display_status STRING,
    fulfillment_created_at TIMESTAMP,
    fulfillment_updated_at TIMESTAMP,
    in_transit_at TIMESTAMP,
    delivered_at TIMESTAMP,
    estimated_delivery_at TIMESTAMP,
    fulfillment_location_id STRING,
    fulfillment_location_name STRING,
    total_quantity BIGINT,
    requires_shipping BOOLEAN,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_fulfillment_item (
    fulfillment_line_item_id STRING,
    fulfillment_id STRING,
    order_id STRING,
    line_item_id STRING,
    fulfilled_quantity BIGINT,
    parent_fulfillment_updated_at TIMESTAMP,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


CREATE TABLE IF NOT EXISTS polaris.source.shopify_fulfillment_event (
    fulfillment_event_id STRING,
    fulfillment_id STRING,
    order_id STRING,
    event_status STRING,
    event_created_at TIMESTAMP,
    event_time TIMESTAMP,
    estimated_delivery_at TIMESTAMP,
    event_message STRING,
    city STRING,
    province STRING,
    country STRING,
    zip STRING,
    latitude DOUBLE,
    longitude DOUBLE,
    parent_fulfillment_updated_at TIMESTAMP,
    record_hash STRING,
    first_observed_at TIMESTAMP,
    last_observed_at TIMESTAMP,
    first_source_updated_at TIMESTAMP,
    last_source_updated_at TIMESTAMP,
    extracted_at TIMESTAMP,
    batch_id STRING
)
USING iceberg;


-- Phase 3B write ordering: keep the mutable execution clock out of the partition spec.
-- These Business-Version tables stay unpartitioned. Re-observing the same content
-- version advances last_source_updated_at, so hard partitioning on that mutable clock
-- would cause partition movement. Write ordering gives a softer locality contract.
ALTER TABLE polaris.source.shopify_order
WRITE ORDERED BY last_source_updated_at, order_id, record_hash;

ALTER TABLE polaris.source.shopify_order_item
WRITE ORDERED BY last_source_updated_at, line_item_id, record_hash;

ALTER TABLE polaris.source.shopify_line_item_discount_allocation
WRITE ORDERED BY last_source_updated_at, line_item_id, discount_application_index, record_hash;

ALTER TABLE polaris.source.shopify_transaction
WRITE ORDERED BY last_source_updated_at, transaction_id, record_hash;

ALTER TABLE polaris.source.shopify_refund
WRITE ORDERED BY last_source_updated_at, refund_id, record_hash;

ALTER TABLE polaris.source.shopify_refund_item
WRITE ORDERED BY last_source_updated_at, refund_line_item_id, record_hash;

ALTER TABLE polaris.source.shopify_refund_transaction
WRITE ORDERED BY last_source_updated_at, refund_id, transaction_id, record_hash;

ALTER TABLE polaris.source.shopify_fulfillment
WRITE ORDERED BY last_source_updated_at, fulfillment_id, record_hash;

ALTER TABLE polaris.source.shopify_fulfillment_item
WRITE ORDERED BY last_source_updated_at, fulfillment_line_item_id, record_hash;

ALTER TABLE polaris.source.shopify_fulfillment_event
WRITE ORDERED BY last_source_updated_at, fulfillment_event_id, record_hash;
