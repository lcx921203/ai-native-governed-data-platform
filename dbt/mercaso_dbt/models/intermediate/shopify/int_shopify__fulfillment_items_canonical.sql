{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='fulfillment_line_item_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{# Shopify FulfillmentLineItem Current State. Execution Window discovers changed keys only; complete Current candidates are ranked per affected key. #}

with source_candidates as (

    select
        fulfillment_line_item_id,
        fulfillment_id,
        order_id,
        line_item_id,
        fulfilled_quantity,
        source_updated_at,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__fulfillment_items') }}
    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct fulfillment_line_item_id
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}
    union all
    select
        current.fulfillment_line_item_id,
        current.fulfillment_id,
        current.order_id,
        current.line_item_id,
        current.fulfilled_quantity,
        current.source_updated_at,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.fulfillment_line_item_id = changed.fulfillment_line_item_id
    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by fulfillment_line_item_id
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    fulfillment_line_item_id,
    fulfillment_id,
    order_id,
    line_item_id,
    fulfilled_quantity,
    source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
