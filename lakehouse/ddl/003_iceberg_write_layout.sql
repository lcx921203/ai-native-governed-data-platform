-- Phase 3B · Iceberg physical write layout
--
-- Run AFTER dbt has materialized the analytics tables.
-- Raw / Structured Source write layout is applied during 001/002 schema bootstrap.
-- This file runs AFTER dbt materialization and owns dbt-produced table write layout. Changing a sort order does not rewrite old files.
-- Existing historical files should be compacted/re-sorted only through an explicit
-- maintenance operation after runtime evidence shows that the benefit justifies it.

-- -----------------------------------------------------------------------------
-- Canonical Current State
-- Also deliberately UNPARTITIONED by source_updated_at. These tables hold one current
-- row per business key and their technical source clock moves whenever that key changes.
-- Ordering keeps recent technical windows together while retaining the business key as
-- the secondary locality dimension.
-- -----------------------------------------------------------------------------
ALTER TABLE polaris.analytics.int_shopify__orders_canonical
WRITE ORDERED BY source_updated_at, order_id;

ALTER TABLE polaris.analytics.int_shopify__order_items_canonical
WRITE ORDERED BY source_updated_at, line_item_id;

ALTER TABLE polaris.analytics.int_shopify__discount_allocations_canonical
WRITE ORDERED BY source_updated_at, line_item_id, discount_application_index;

ALTER TABLE polaris.analytics.int_shopify__order_item_discounts
WRITE ORDERED BY source_updated_at, line_item_id, currency_code;

ALTER TABLE polaris.analytics.int_shopify__transactions_canonical
WRITE ORDERED BY source_updated_at, transaction_id;

ALTER TABLE polaris.analytics.int_shopify__refunds_canonical
WRITE ORDERED BY source_updated_at, refund_id;

ALTER TABLE polaris.analytics.int_shopify__refund_items_canonical
WRITE ORDERED BY source_updated_at, refund_line_item_id;

ALTER TABLE polaris.analytics.int_shopify__fulfillments_canonical
WRITE ORDERED BY source_updated_at, fulfillment_id;

ALTER TABLE polaris.analytics.int_shopify__fulfillment_items_canonical
WRITE ORDERED BY source_updated_at, fulfillment_line_item_id;

ALTER TABLE polaris.analytics.int_shopify__fulfillment_events_canonical
WRITE ORDERED BY source_updated_at, fulfillment_event_id;

-- -----------------------------------------------------------------------------
-- Business Marts
-- dbt owns hidden business-time partition specs for the seven tables below.
-- Within each partition, write the Mart grain in a stable local order.
-- -----------------------------------------------------------------------------
ALTER TABLE polaris.analytics.orders
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY order_id;

ALTER TABLE polaris.analytics.order_lifecycle_snapshot
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY order_id;

ALTER TABLE polaris.analytics.order_items
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY line_item_id;

ALTER TABLE polaris.analytics.payment_transactions
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY payment_transaction_id;

ALTER TABLE polaris.analytics.refunds
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY refund_id;

ALTER TABLE polaris.analytics.refund_items
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY refund_line_item_id;

ALTER TABLE polaris.analytics.fulfillments
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY fulfillment_id;

ALTER TABLE polaris.analytics.fulfillment_events
WRITE DISTRIBUTED BY PARTITION
LOCALLY ORDERED BY fulfillment_event_id;

-- FulfillmentItem is intentionally not day-partitioned yet. Its dominant analysis
-- clocks (in_transit_at / delivered_at) are lifecycle fields that can transition from
-- NULL to a value and do not have one universally correct physical day. Keep it
-- unpartitioned and cluster future writes by lifecycle time instead.
ALTER TABLE polaris.analytics.fulfillment_items
WRITE ORDERED BY in_transit_at ASC NULLS LAST,
                 delivered_at ASC NULLS LAST,
                 fulfillment_line_item_id;
