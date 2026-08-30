{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='fulfillment_event_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{# Shopify FulfillmentEvent Current State. Execution Window discovers changed keys only; complete Current candidates are ranked per affected key. #}

with source_candidates as (

    select
        fulfillment_event_id,
        fulfillment_id,
        order_id,
        event_status,
        event_created_at,
        event_time,
        estimated_delivery_at,
        event_message,
        city,
        province,
        country,
        zip,
        latitude,
        longitude,
        source_updated_at,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__fulfillment_events') }}
    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct fulfillment_event_id
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}
    union all
    select
        current.fulfillment_event_id,
        current.fulfillment_id,
        current.order_id,
        current.event_status,
        current.event_created_at,
        current.event_time,
        current.estimated_delivery_at,
        current.event_message,
        current.city,
        current.province,
        current.country,
        current.zip,
        current.latitude,
        current.longitude,
        current.source_updated_at,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.fulfillment_event_id = changed.fulfillment_event_id
    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by fulfillment_event_id
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    fulfillment_event_id,
    fulfillment_id,
    order_id,
    event_status,
    event_created_at,
    event_time,
    estimated_delivery_at,
    event_message,
    city,
    province,
    country,
    zip,
    latitude,
    longitude,
    source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
