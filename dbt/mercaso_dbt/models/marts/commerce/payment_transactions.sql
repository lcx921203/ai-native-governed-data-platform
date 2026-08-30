{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='payment_transaction_id',
    file_format='iceberg',
    partition_by='days(transaction_processed_at)',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

{#
  Payment Transaction Mart — Affected-Key Propagation

  A payment row can be affected by:
    1. the Transaction itself being re-observed / changed;
    2. its parent Order changing, because this Mart carries order_time.

  Convert both window-scoped change paths into affected_transaction_ids first.
  Only then read the complete CURRENT Transaction and Order rows for those keys,
  calculate business measures, and MERGE the affected Mart Grain.
#}

with changed_transactions as (

    select distinct transaction_id
    from {{ ref('int_shopify__transactions_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

changed_orders as (

    select distinct order_id
    from {{ ref('orders') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

transactions_from_changed_orders as (

    select distinct t.transaction_id
    from {{ ref('int_shopify__transactions_canonical') }} t
    inner join changed_orders o
        on t.order_id = o.order_id

),

affected_transaction_ids as (

    select transaction_id from changed_transactions
    union
    select transaction_id from transactions_from_changed_orders

),

affected_transactions as (

    select t.*
    from {{ ref('int_shopify__transactions_canonical') }} t
    inner join affected_transaction_ids affected
        on t.transaction_id = affected.transaction_id

),

affected_order_ids as (

    select distinct order_id
    from affected_transactions

),

affected_orders as (

    -- Once a Transaction Grain is affected, read the complete CURRENT parent Order.
    -- Do not reapply the execution window here: the parent may be older than the
    -- Transaction change that caused this Mart row to be recomputed.
    select o.*
    from {{ ref('orders') }} o
    inner join affected_order_ids affected
        on o.order_id = affected.order_id

),

modeled as (

    select
        t.transaction_id as payment_transaction_id,
        t.order_id,
        t.parent_transaction_id,
        t.transaction_kind,
        t.transaction_status,
        t.gateway,
        t.transaction_created_at,
        t.transaction_processed_at,
        o.order_time,
        t.transaction_amount,
        t.currency_code,
        t.is_test,
        t.error_code,

        case
            when t.transaction_status = 'SUCCESS'
             and t.transaction_kind in ('AUTHORIZATION', 'EMV_AUTHORIZATION')
             and not t.is_test
            then t.transaction_amount
            else cast(0 as decimal(18,2))
        end as authorized_amount,

        case
            when t.transaction_status = 'SUCCESS'
             and t.transaction_kind in ('CAPTURE', 'SALE')
             and not t.is_test
            then t.transaction_amount
            else cast(0 as decimal(18,2))
        end as collected_amount,

        case
            when t.transaction_status = 'SUCCESS'
             and t.transaction_kind = 'REFUND'
             and not t.is_test
            then t.transaction_amount
            else cast(0 as decimal(18,2))
        end as successful_refund_amount,

        case
            when t.transaction_status = 'SUCCESS'
             and t.transaction_kind in ('CAPTURE', 'SALE')
             and not t.is_test
            then 1 else 0
        end as successful_collection_flag,

        case
            when t.transaction_status in ('FAILURE', 'ERROR')
             and not t.is_test
            then 1 else 0
        end as failed_transaction_flag,

        greatest(
            t.source_updated_at,
            o.source_updated_at
        ) as source_updated_at,

        greatest(
            t.extracted_at,
            o.source_extracted_at
        ) as source_extracted_at

    from affected_transactions t
    inner join affected_orders o
        on t.order_id = o.order_id

)

select *
from modeled
