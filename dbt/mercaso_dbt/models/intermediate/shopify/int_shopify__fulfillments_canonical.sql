{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='fulfillment_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{#
  Fulfillment Canonical Current State

  The execution window discovers only fulfillment_id values whose source version
  clock moved in this run. For each changed key, compare the new/re-observed source
  candidates with the previously materialized Current Row and MERGE the winner.
#}

with source_candidates as (

    select
        fulfillment_id,
        order_id,
        fulfillment_name,
        fulfillment_status,
        display_status,
        fulfillment_created_at,
        source_updated_at,
        in_transit_at,
        delivered_at,
        estimated_delivery_at,
        fulfillment_location_id,
        fulfillment_location_name,
        total_quantity,
        requires_shipping,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__fulfillments') }}

    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct fulfillment_id
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}

    union all

    select
        current.fulfillment_id,
        current.order_id,
        current.fulfillment_name,
        current.fulfillment_status,
        current.display_status,
        current.fulfillment_created_at,
        current.source_updated_at,
        current.in_transit_at,
        current.delivered_at,
        current.estimated_delivery_at,
        current.fulfillment_location_id,
        current.fulfillment_location_name,
        current.total_quantity,
        current.requires_shipping,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.fulfillment_id = changed.fulfillment_id

    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by fulfillment_id
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    fulfillment_id,
    order_id,
    fulfillment_name,
    fulfillment_status,
    display_status,
    fulfillment_created_at,
    source_updated_at,
    in_transit_at,
    delivered_at,
    estimated_delivery_at,
    fulfillment_location_id,
    fulfillment_location_name,
    total_quantity,
    requires_shipping,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
