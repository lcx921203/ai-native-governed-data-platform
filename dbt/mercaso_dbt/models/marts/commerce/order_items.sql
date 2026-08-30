{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='line_item_id',
    file_format='iceberg',
    partition_by='days(order_time)',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

{#
  OrderItem Mart — Affected-Key Propagation

  A LineItem Mart row can change for three independent reasons:

  1. the LineItem itself changed;
  2. its parent Order changed (for example store_id / order_time propagated here);
  3. one of its DiscountAllocation rows changed.

  Therefore the execution window is first converted into affected_line_item_ids.
  Only after the affected Grain is known do we read the current OrderItem / Order /
  Discount rows and perform the joins.

  This avoids the anti-pattern:
      full inputs -> full joins -> final window filter.
#}

{# CTE changed_line_items：直接发生变化的 LineItem Grain。 #}
with changed_line_items as (

    select distinct line_item_id
    from {{ ref('int_shopify__order_items_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE changed_orders：父 Order 变化也可能改变 store_id / order_time 等下游字段。 #}
changed_orders as (

    select distinct order_id
    from {{ ref('orders') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE line_items_from_changed_orders：把父订单变化传播到对应 line_item_id。 #}
line_items_from_changed_orders as (

    select distinct i.line_item_id
    from {{ ref('int_shopify__order_items_canonical') }} i
    inner join changed_orders o
        on i.order_id = o.order_id

),

{# CTE changed_discounts：折扣聚合变化会改变 OrderItem 的 discount/net sales 结果。 #}
changed_discounts as (

    -- Consume the governed incremental LineItem discount aggregate instead of
    -- bypassing Current State and rediscovering changes from Staging.
    select distinct line_item_id
    from {{ ref('int_shopify__order_item_discounts') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE affected_line_item_ids：三路变化取并集，确定本次真正需要重算的 Mart Grain。 #}
affected_line_item_ids as (

    select line_item_id from changed_line_items
    union
    select line_item_id from line_items_from_changed_orders
    union
    select line_item_id from changed_discounts

),

{# CTE affected_order_items：回读受影响 LineItem 的完整 Current 行，不再窗口截断。 #}
affected_order_items as (

    select i.*
    from {{ ref('int_shopify__order_items_canonical') }} i
    inner join affected_line_item_ids a
        on i.line_item_id = a.line_item_id

),

{# CTE affected_order_ids：从受影响 LineItem 推导需要回读的父 Order。 #}
affected_order_ids as (

    select distinct order_id
    from affected_order_items

),

{# CTE affected_orders：读取完整 Order Current Context，提供 store/time 等父级字段。 #}
affected_orders as (

    -- Do not filter Orders by the execution window here: a LineItem may be affected
    -- by its own change while its parent Order is older.  We need the current parent.
    select o.*
    from {{ ref('orders') }} o
    inner join affected_order_ids a
        on o.order_id = a.order_id

),

{# CTE affected_discounts：读取受影响 LineItem 的完整当前折扣聚合。 #}
affected_discounts as (

    -- Current discount amount for the affected LineItems only.
    select d.*
    from {{ ref('int_shopify__order_item_discounts') }} d
    inner join affected_line_item_ids a
        on d.line_item_id = a.line_item_id

),

{# CTE modeled：安全 join 回一 LineItem 一行，并计算最终销售/折扣字段。 #}
modeled as (

    select
        i.line_item_id,
        i.order_id,
        o.store_id,
        i.item_id,
        i.variant_id,
        i.sku,
        i.item_title,

        o.order_time,

        i.ordered_quantity,
        i.current_quantity,
        i.refundable_quantity,
        i.unfulfilled_quantity,

        i.original_unit_price,
        i.gross_sales_amount,

        coalesce(
            d.discount_amount,
            cast(0 as decimal(18,2))
        ) as discount_amount,

        i.gross_sales_amount
          - coalesce(d.discount_amount, cast(0 as decimal(18,2)))
          as sales_before_reversal_amount,

        i.currency_code,

        greatest(
            i.source_updated_at,
            o.source_updated_at,
            coalesce(d.source_updated_at, i.source_updated_at)
        ) as source_updated_at,

        greatest(
            i.extracted_at,
            o.source_extracted_at,
            coalesce(d.extracted_at, i.extracted_at)
        ) as source_extracted_at

    from affected_order_items i
    inner join affected_orders o
        on i.order_id = o.order_id
    left join affected_discounts d
        on i.line_item_id = d.line_item_id
       and i.currency_code = d.currency_code

)

select *
from modeled
