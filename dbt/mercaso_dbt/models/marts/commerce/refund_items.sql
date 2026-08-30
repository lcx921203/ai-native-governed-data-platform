{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='refund_line_item_id',
    file_format='iceberg',
    partition_by='days(refund_time)',
    on_schema_change='fail',
    tags=['shopify_windowed']
) }}

{#
  RefundItem Mart — Multi-Upstream Affected-Key Propagation

  One RefundItem Mart row can change because:
    1. the RefundLineItem itself changed / was re-observed;
    2. its parent Refund changed (refund_time / source clock);
    3. the referenced OrderItem changed (item_id / order_time / source clock).

  Discover all affected refund_line_item_id values first. After the Mart grain is
  known, read the complete CURRENT rows from RefundItem, Refund, and OrderItem without
  reapplying the execution window, then perform the business join and MERGE.
#}

{# CTE changed_refund_items：直接变化的 RefundLineItem Grain。 #}
with changed_refund_items as (

    select distinct refund_line_item_id
    from {{ ref('int_shopify__refund_items_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE changed_refunds：父 Refund 变化会影响 refund_time / source clock。 #}
changed_refunds as (

    select distinct refund_id
    from {{ ref('refunds') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE refund_items_from_changed_refunds：把父 Refund 变化传播到退款商品 Grain。 #}
refund_items_from_changed_refunds as (

    select distinct ri.refund_line_item_id
    from {{ ref('int_shopify__refund_items_canonical') }} ri
    inner join changed_refunds r
        on ri.refund_id = r.refund_id

),

{# CTE changed_order_items：原 OrderItem 变化会影响 item_id / order_time 等关联字段。 #}
changed_order_items as (

    select distinct line_item_id
    from {{ ref('order_items') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE refund_items_from_changed_order_items：把原订单商品变化传播到退款商品。 #}
refund_items_from_changed_order_items as (

    select distinct ri.refund_line_item_id
    from {{ ref('int_shopify__refund_items_canonical') }} ri
    inner join changed_order_items oi
        on ri.line_item_id = oi.line_item_id

),

{# CTE affected_refund_line_item_ids：三路变化合并后确定本次重算 Grain。 #}
affected_refund_line_item_ids as (

    select refund_line_item_id from changed_refund_items
    union
    select refund_line_item_id from refund_items_from_changed_refunds
    union
    select refund_line_item_id from refund_items_from_changed_order_items

),

{# CTE affected_refund_items：回读受影响 RefundLineItem 的完整 Current 行。 #}
affected_refund_items as (

    select ri.*
    from {{ ref('int_shopify__refund_items_canonical') }} ri
    inner join affected_refund_line_item_ids affected
        on ri.refund_line_item_id = affected.refund_line_item_id

),

{# CTE affected_refund_ids：从受影响退款商品推导需要回读的父 Refund。 #}
affected_refund_ids as (

    select distinct refund_id
    from affected_refund_items

),

{# CTE affected_refunds：读取完整 Refund Current Context。 #}
affected_refunds as (

    -- Read complete CURRENT Refund rows for the affected Mart grains. The parent
    -- Refund may be older than the child change that triggered recomputation.
    select r.*
    from {{ ref('refunds') }} r
    inner join affected_refund_ids affected
        on r.refund_id = affected.refund_id

),

{# CTE affected_line_item_ids：推导需要回读的原 OrderItem。 #}
affected_line_item_ids as (

    select distinct line_item_id
    from affected_refund_items

),

{# CTE affected_order_items：读取完整 OrderItem Current Context，避免窗口 join 丢历史上下文。 #}
affected_order_items as (

    -- Same rule for OrderItem: after affected RefundItem grains are known, fetch the
    -- complete CURRENT referenced OrderItem without another Window predicate.
    select oi.*
    from {{ ref('order_items') }} oi
    inner join affected_line_item_ids affected
        on oi.line_item_id = affected.line_item_id

),

{# CTE modeled：安全回连 Refund / OrderItem，形成一 RefundLineItem 一行的最终 Mart。 #}
modeled as (

    select
        ri.refund_line_item_id,
        ri.refund_id,
        ri.order_id,
        ri.line_item_id,
        oi.item_id,
        r.refund_time,
        oi.order_time,
        ri.quantity as refund_quantity,
        ri.restocked,
        ri.restock_type,
        ri.subtotal_amount as sales_reversal_amount,
        ri.tax_amount as refund_tax_amount,
        ri.subtotal_amount + ri.tax_amount as refund_amount_including_tax,
        ri.currency_code,

        greatest(
            ri.source_updated_at,
            r.source_updated_at,
            oi.source_updated_at
        ) as source_updated_at,

        greatest(
            ri.extracted_at,
            r.source_extracted_at,
            oi.source_extracted_at
        ) as source_extracted_at

    from affected_refund_items ri
    inner join affected_refunds r
        on ri.refund_id = r.refund_id
    inner join affected_order_items oi
        on ri.line_item_id = oi.line_item_id

)

select *
from modeled
