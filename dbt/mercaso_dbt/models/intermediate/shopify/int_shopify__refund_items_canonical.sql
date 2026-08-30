{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='refund_line_item_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{#
  Canonical Current State — Shopify RefundLineItem

  The Shopify execution window discovers refund_line_item_id values that changed or
  were re-observed. Only those candidates are compared with their existing Current
  Rows in {{ this }}. The model therefore avoids a full-history row_number() on every
  partition run while preserving Business Version re-observation semantics.
#}

with source_candidates as (

    select
        refund_line_item_id,
        refund_id,
        order_id,
        line_item_id,
        quantity,
        restocked,
        restock_type,
        subtotal_amount,
        tax_amount,
        currency_code,
        source_updated_at,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__refund_items') }}

    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct refund_line_item_id
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}

    union all

    select
        current.refund_line_item_id,
        current.refund_id,
        current.order_id,
        current.line_item_id,
        current.quantity,
        current.restocked,
        current.restock_type,
        current.subtotal_amount,
        current.tax_amount,
        current.currency_code,
        current.source_updated_at,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.refund_line_item_id = changed.refund_line_item_id

    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by refund_line_item_id
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    refund_line_item_id,
    refund_id,
    order_id,
    line_item_id,
    quantity,
    restocked,
    restock_type,
    subtotal_amount,
    tax_amount,
    currency_code,
    source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
