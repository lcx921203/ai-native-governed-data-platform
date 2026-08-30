{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['line_item_id', 'discount_application_index'],
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{#
  Canonical Current State — DiscountAllocation Grain

  Business key:
      line_item_id + discount_application_index

  The execution window is used only to discover changed allocation keys.  On an
  incremental run, each changed key compares its new/re-observed Source Versions with
  the Current Row already stored in {{ this }}.  We never run row_number() over the
  complete historical DiscountAllocation version set for every daily partition.

  Snapshot-member disappearance / tombstone semantics are intentionally NOT inferred
  here.  They require a complete Shopify nested snapshot contract and remain a
  production boundary rather than being guessed from a missing row.
#}

with source_candidates as (

    select
        order_id,
        line_item_id,
        discount_application_index,
        discount_application_type,
        allocation_method,
        target_selection,
        target_type,
        allocated_amount,
        currency_code,
        source_updated_at,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__line_item_discount_allocations') }}

    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct
        line_item_id,
        discount_application_index
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}

    union all

    select
        current.order_id,
        current.line_item_id,
        current.discount_application_index,
        current.discount_application_type,
        current.allocation_method,
        current.target_selection,
        current.target_type,
        current.allocated_amount,
        current.currency_code,
        current.source_updated_at,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.line_item_id = changed.line_item_id
       and current.discount_application_index = changed.discount_application_index

    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by
                line_item_id,
                discount_application_index
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    order_id,
    line_item_id,
    discount_application_index,
    discount_application_type,
    allocation_method,
    target_selection,
    target_type,
    allocated_amount,
    currency_code,
    source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
