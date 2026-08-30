{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='line_item_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{# Shopify LineItem Current State. Execution Window discovers changed keys only; complete Current candidates are ranked per affected key. #}

with source_candidates as (

    select
        line_item_id,
        order_id,
        item_id,
        variant_id,
        sku,
        item_title,
        ordered_quantity,
        current_quantity,
        refundable_quantity,
        unfulfilled_quantity,
        original_unit_price,
        gross_sales_amount,
        source_line_discount_amount,
        currency_code,
        source_updated_at,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__order_items') }}
    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct line_item_id
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}
    union all
    select
        current.line_item_id,
        current.order_id,
        current.item_id,
        current.variant_id,
        current.sku,
        current.item_title,
        current.ordered_quantity,
        current.current_quantity,
        current.refundable_quantity,
        current.unfulfilled_quantity,
        current.original_unit_price,
        current.gross_sales_amount,
        current.source_line_discount_amount,
        current.currency_code,
        current.source_updated_at,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.line_item_id = changed.line_item_id
    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by line_item_id
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    line_item_id,
    order_id,
    item_id,
    variant_id,
    sku,
    item_title,
    ordered_quantity,
    current_quantity,
    refundable_quantity,
    unfulfilled_quantity,
    original_unit_price,
    gross_sales_amount,
    source_line_discount_amount,
    currency_code,
    source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
