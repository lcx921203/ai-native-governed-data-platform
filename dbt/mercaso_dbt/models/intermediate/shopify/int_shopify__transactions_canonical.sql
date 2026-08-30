{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='transaction_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{#
  Canonical Current State — Shopify OrderTransaction

  OrderTransaction does not expose an independent updated_at in the current
  extraction contract, so Structured Source versions are ordered by the parent
  Order.updatedAt observation clock. Staging exposes the merged
  last_source_updated_at as source_updated_at.

  The execution window is used to discover changed transaction_id values only.
  For each changed key, rank the new/re-observed candidates together with that
  key's previously materialized Current Row, then MERGE one winner per transaction.
#}

with source_candidates as (

    select
        transaction_id,
        order_id,
        parent_transaction_id,
        transaction_kind,
        transaction_status,
        gateway,
        transaction_created_at,
        transaction_processed_at,
        transaction_amount,
        currency_code,
        is_test,
        error_code,
        source_updated_at,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__transactions') }}

    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct transaction_id
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}

    union all

    select
        current.transaction_id,
        current.order_id,
        current.parent_transaction_id,
        current.transaction_kind,
        current.transaction_status,
        current.gateway,
        current.transaction_created_at,
        current.transaction_processed_at,
        current.transaction_amount,
        current.currency_code,
        current.is_test,
        current.error_code,
        current.source_updated_at,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.transaction_id = changed.transaction_id

    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by transaction_id
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    transaction_id,
    order_id,
    parent_transaction_id,
    transaction_kind,
    transaction_status,
    gateway,
    transaction_created_at,
    transaction_processed_at,
    transaction_amount,
    currency_code,
    is_test,
    error_code,
    source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
