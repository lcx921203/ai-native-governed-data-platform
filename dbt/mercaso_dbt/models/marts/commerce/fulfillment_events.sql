{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='fulfillment_event_id',
    file_format='iceberg',
    partition_by='days(event_time)',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

{#
  FulfillmentEvent Mart — Parent Propagation

  An Event Mart row changes when either the event itself changes / is re-observed,
  or its parent Fulfillment Mart row changes (for example store_id propagated from
  Order). event_time remains business event time; source_updated_at is technical.
#}

with changed_events as (

    select distinct fulfillment_event_id
    from {{ ref('int_shopify__fulfillment_events_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

changed_fulfillments as (

    select distinct fulfillment_id
    from {{ ref('fulfillments') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

events_from_changed_fulfillments as (

    select distinct e.fulfillment_event_id
    from {{ ref('int_shopify__fulfillment_events_canonical') }} e
    inner join changed_fulfillments f
        on e.fulfillment_id = f.fulfillment_id

),

affected_fulfillment_event_ids as (

    select fulfillment_event_id from changed_events
    union
    select fulfillment_event_id from events_from_changed_fulfillments

),

affected_events as (

    select e.*
    from {{ ref('int_shopify__fulfillment_events_canonical') }} e
    inner join affected_fulfillment_event_ids affected
        on e.fulfillment_event_id = affected.fulfillment_event_id

),

affected_fulfillment_ids as (

    select distinct fulfillment_id
    from affected_events

),

affected_fulfillments as (

    select f.*
    from {{ ref('fulfillments') }} f
    inner join affected_fulfillment_ids affected
        on f.fulfillment_id = affected.fulfillment_id

),

modeled as (

    select
        e.fulfillment_event_id,
        e.fulfillment_id,
        e.order_id,
        f.store_id,
        e.event_status,
        e.event_time,
        e.event_created_at,
        e.estimated_delivery_at,
        e.city,
        e.province,
        e.country,
        e.zip,
        e.latitude,
        e.longitude,
        e.event_message,
        greatest(e.source_updated_at, f.source_updated_at) as source_updated_at,
        greatest(e.extracted_at, f.source_extracted_at) as source_extracted_at
    from affected_events e
    inner join affected_fulfillments f
        on e.fulfillment_id = f.fulfillment_id

)

select *
from modeled
