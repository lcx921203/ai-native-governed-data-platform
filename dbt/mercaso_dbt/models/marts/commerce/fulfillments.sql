{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='fulfillment_id',
    file_format='iceberg',
    partition_by='days(fulfillment_created_at)',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

with changed_fulfillments as (
    select distinct fulfillment_id
    from {{ ref('int_shopify__fulfillments_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}
),
changed_orders as (
    select distinct order_id
    from {{ ref('orders') }}
    where {{ shopify_window_predicate('source_updated_at') }}
),
fulfillments_from_changed_orders as (
    select distinct f.fulfillment_id
    from {{ ref('int_shopify__fulfillments_canonical') }} f
    inner join changed_orders o on f.order_id = o.order_id
),
affected_fulfillment_ids as (
    select fulfillment_id from changed_fulfillments
    union
    select fulfillment_id from fulfillments_from_changed_orders
),
affected_fulfillments as (
    select f.*
    from {{ ref('int_shopify__fulfillments_canonical') }} f
    inner join affected_fulfillment_ids a on f.fulfillment_id = a.fulfillment_id
),
affected_order_ids as (
    select distinct order_id from affected_fulfillments
),
affected_orders as (
    select o.*
    from {{ ref('orders') }} o
    inner join affected_order_ids a on o.order_id = a.order_id
),
modeled as (
    select
        f.fulfillment_id, f.order_id, o.store_id,
        f.fulfillment_name, f.fulfillment_status, f.display_status,
        o.order_time, f.fulfillment_created_at, f.in_transit_at, f.delivered_at,
        f.estimated_delivery_at, f.fulfillment_location_id, f.fulfillment_location_name,
        f.total_quantity, f.requires_shipping,
        case when f.in_transit_at is not null then 1 else 0 end as shipped_flag,
        case when f.delivered_at is not null then 1 else 0 end as delivered_flag,
        case when f.in_transit_at is not null and f.in_transit_at >= o.order_time
             then (unix_timestamp(f.in_transit_at)-unix_timestamp(o.order_time))/60.0 end as time_to_ship_minutes,
        case when f.delivered_at is not null and f.in_transit_at is not null and f.delivered_at >= f.in_transit_at
             then (unix_timestamp(f.delivered_at)-unix_timestamp(f.in_transit_at))/60.0 end as delivery_duration_minutes,
        greatest(f.source_updated_at, o.source_updated_at) as source_updated_at,
        greatest(f.extracted_at, o.source_extracted_at) as source_extracted_at
    from affected_fulfillments f
    inner join affected_orders o on f.order_id = o.order_id
)
select * from modeled
