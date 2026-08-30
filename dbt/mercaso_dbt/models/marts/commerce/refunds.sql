{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='refund_id',
    file_format='iceberg',
    partition_by='days(refund_time)',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

with affected_refunds as (
    select
        refund_id, order_id, created_at, processed_at, source_updated_at,
        total_refunded_amount, currency_code, extracted_at
    from {{ ref('int_shopify__refunds_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}
)

select
    refund_id,
    order_id,
    created_at,
    processed_at as refund_time,
    source_updated_at,
    total_refunded_amount,
    currency_code,
    extracted_at as source_extracted_at
from affected_refunds
