{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key=['line_item_id', 'currency_code'],
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

{#
  DiscountAllocation Grain
      LineItem × DiscountApplication
  -> LineItem × Currency Grain

  This is an incremental re-grain, not a full-table GROUP BY View.

  1. The window discovers which LineItems own changed Current DiscountAllocations.
  2. For those LineItems, read their complete CURRENT allocation set from the
     canonical current-state table (not merely today's changed rows).
  3. Re-aggregate only those LineItems and MERGE the result.

  Reading the complete current set after key discovery is important: when one
  allocation changes, the LineItem discount total still needs all of that LineItem's
  other unchanged allocations.
#}

with affected_line_item_ids as (

    select distinct line_item_id
    from {{ ref('int_shopify__discount_allocations_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

current_allocations_for_affected_items as (

    select a.*
    from {{ ref('int_shopify__discount_allocations_canonical') }} a
    inner join affected_line_item_ids affected
        on a.line_item_id = affected.line_item_id

),

aggregated as (

    select
        line_item_id,
        currency_code,
        sum(allocated_amount) as discount_amount,
        max(source_updated_at) as source_updated_at,
        max(extracted_at) as extracted_at
    from current_allocations_for_affected_items
    group by
        line_item_id,
        currency_code

)

select *
from aggregated
