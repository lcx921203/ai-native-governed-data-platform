{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='order_id',
    file_format='iceberg',
    partition_by='days(order_time)',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

{#
  Order Mart — one row per Order.

  The Canonical Current State table is persistent and contains every current Order.
  Therefore the Mart must still use the Dagster/dbt source-update window to select
  only Orders affected by this execution window before projecting business columns.

  Execution Window decides what is recomputed.
  order_time remains the business time used by analytics / MetricFlow.
#}

with affected_orders as (

    select
        order_id,
        store_id,
        order_created_at,
        order_processed_at,
        cancelled_at,
        closed_at,
        financial_status,
        fulfillment_status,
        currency_code,
        original_total_amount,
        current_total_amount,
        source_updated_at,
        extracted_at
    from {{ ref('int_shopify__orders_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

)

select
    order_id,
    store_id,
    order_created_at as order_time,
    order_processed_at as processed_at,
    cancelled_at,
    closed_at,
    financial_status,
    fulfillment_status,
    currency_code,
    original_total_amount,
    current_total_amount,
    source_updated_at,
    extracted_at as source_extracted_at
from affected_orders
