{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='fulfillment_line_item_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

{#
  FulfillmentItem Mart — Multi-Upstream Affected-Key Propagation

  A FulfillmentLineItem Mart row can change because:
    1. the FulfillmentLineItem itself changed;
    2. its parent Fulfillment Mart row changed;
    3. the referenced OrderItem Mart row changed.

  Parent Order changes are already propagated through Fulfillment / OrderItem Marts,
  so this model consumes those direct upstream change contracts instead of repeating
  the whole dependency graph here.
#}

with changed_fulfillment_items as (

    select distinct fulfillment_line_item_id
    from {{ ref('int_shopify__fulfillment_items_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

changed_fulfillments as (

    select distinct fulfillment_id
    from {{ ref('fulfillments') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

fulfillment_items_from_changed_fulfillments as (

    select distinct fi.fulfillment_line_item_id
    from {{ ref('int_shopify__fulfillment_items_canonical') }} fi
    inner join changed_fulfillments f
        on fi.fulfillment_id = f.fulfillment_id

),

changed_order_items as (

    select distinct line_item_id
    from {{ ref('order_items') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

fulfillment_items_from_changed_order_items as (

    select distinct fi.fulfillment_line_item_id
    from {{ ref('int_shopify__fulfillment_items_canonical') }} fi
    inner join changed_order_items oi
        on fi.line_item_id = oi.line_item_id

),

affected_fulfillment_line_item_ids as (

    select fulfillment_line_item_id from changed_fulfillment_items
    union
    select fulfillment_line_item_id from fulfillment_items_from_changed_fulfillments
    union
    select fulfillment_line_item_id from fulfillment_items_from_changed_order_items

),

affected_fulfillment_items as (

    select fi.*
    from {{ ref('int_shopify__fulfillment_items_canonical') }} fi
    inner join affected_fulfillment_line_item_ids affected
        on fi.fulfillment_line_item_id = affected.fulfillment_line_item_id

),

affected_fulfillment_ids as (

    select distinct fulfillment_id
    from affected_fulfillment_items

),

affected_fulfillments as (

    select f.*
    from {{ ref('fulfillments') }} f
    inner join affected_fulfillment_ids affected
        on f.fulfillment_id = affected.fulfillment_id

),

affected_line_item_ids as (

    select distinct line_item_id
    from affected_fulfillment_items

),

affected_order_items as (

    select oi.*
    from {{ ref('order_items') }} oi
    inner join affected_line_item_ids affected
        on oi.line_item_id = affected.line_item_id

),

modeled as (

    select
        fi.fulfillment_line_item_id,
        fi.fulfillment_id,
        fi.order_id,
        fi.line_item_id,
        oi.item_id,
        f.store_id,
        fi.fulfilled_quantity,
        f.fulfillment_created_at,
        f.in_transit_at,
        f.delivered_at,
        f.fulfillment_status,
        case when f.in_transit_at is not null then fi.fulfilled_quantity else 0 end
            as shipped_quantity,
        case when f.delivered_at is not null then fi.fulfilled_quantity else 0 end
            as delivered_quantity,
        greatest(fi.source_updated_at, f.source_updated_at, oi.source_updated_at)
            as source_updated_at,
        greatest(fi.extracted_at, f.source_extracted_at, oi.source_extracted_at)
            as source_extracted_at
    from affected_fulfillment_items fi
    inner join affected_fulfillments f
        on fi.fulfillment_id = f.fulfillment_id
    inner join affected_order_items oi
        on fi.line_item_id = oi.line_item_id

)

select *
from modeled
