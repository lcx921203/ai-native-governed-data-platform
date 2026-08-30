{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='refund_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{#
  Canonical Current State — Shopify Refund

  Structured Source keeps one row per Refund business-content version and Staging
  exposes last_source_updated_at as source_updated_at. The execution window is used
  only to discover refund_id values that were changed / re-observed in this run.

  On incremental runs, compare those candidates with the previously materialized
  Current Row for the same refund_id, rank only that candidate pool, and MERGE one
  winning row per Refund.
#}

with source_candidates as (

    select
        refund_id,
        order_id,
        created_at,
        processed_at,
        source_updated_at,
        total_refunded_amount,
        currency_code,
        record_hash,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__refunds') }}

    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

changed_keys as (

    select distinct refund_id
    from source_candidates

),

candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}

    union all

    select
        current.refund_id,
        current.order_id,
        current.created_at,
        current.processed_at,
        current.source_updated_at,
        current.total_refunded_amount,
        current.currency_code,
        current.record_hash,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.refund_id = changed.refund_id

    {% endif %}

),

ranked as (

    select
        *,
        row_number() over (
            partition by refund_id
            order by
                source_updated_at desc,
                extracted_at desc
        ) as version_rank
    from candidate_pool

)

select
    refund_id,
    order_id,
    created_at,
    processed_at,
    source_updated_at,
    total_refunded_amount,
    currency_code,
    record_hash,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
