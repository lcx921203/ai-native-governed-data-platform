{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='order_id',
    file_format='iceberg',
    partition_by='days(order_time)',
    on_schema_change='fail',
    tags=['shopify_windowed', 'order_lifecycle_snapshot']
) }}

{#
  Order Lifecycle Accumulating Snapshot — one row per Order.

  业务目的：
  - Current State 回答“订单现在是什么状态”；
  - 本模型回答“这个订单已经走过哪些生命周期里程碑，以及这些里程碑什么时候发生”。

  Grain：一个 order_id 一行。

  Incremental / MERGE 边界：
  1. Execution Window 只负责发现“哪些订单受影响”；
  2. Order / Transaction / Refund / Fulfillment / FulfillmentEvent 任一侧变化，
     都会把对应 order_id 放进 affected_order_ids；
  3. 一旦 order_id 受影响，就读取这个订单当前完整的子事实，再重算整条生命周期快照；
  4. dbt 最后按 order_id MERGE，因此重复执行同一窗口不会新增重复订单行。

  工程边界：
  - 本模型只从当前已治理的 Order / Transaction / Refund / Fulfillment / Event 事实推导里程碑；
  - 源端没有权威时间就保持 NULL，不从状态文本或相邻时间猜测。

  事实边界：
  - paid_at 只来自 SUCCESS 的 CAPTURE / SALE，Authorization 不等于已经收款；
  - 当前源契约没有 picked_at，因此本模型不会凭状态或时间差伪造拣货时间；
  - in_transit / delivered 优先使用 Fulfillment 自身时间，同时允许使用同义的
    FulfillmentEvent 业务事件时间作补充；
  - source_updated_at / source_extracted_at 是技术时钟，不是业务里程碑。
#}

{# CTE changed_orders：Order Current State 在窗口内变化的 order_id。 #}
with changed_orders as (

    select distinct order_id
    from {{ ref('int_shopify__orders_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE changed_transactions：支付/授权事实变化也会推动订单生命周期重算。 #}
changed_transactions as (

    select distinct order_id
    from {{ ref('int_shopify__transactions_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE changed_refunds：退款事实变化会影响 first_refund_at / refund_count。 #}
changed_refunds as (

    select distinct order_id
    from {{ ref('int_shopify__refunds_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE changed_fulfillments：履约对象变化会影响发货、运输、送达里程碑。 #}
changed_fulfillments as (

    select distinct order_id
    from {{ ref('int_shopify__fulfillments_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE changed_fulfillment_events：事件级 IN_TRANSIT / DELIVERED 变化也要传播到订单 Grain。 #}
changed_fulfillment_events as (

    select distinct order_id
    from {{ ref('int_shopify__fulfillment_events_canonical') }}
    where {{ shopify_window_predicate('source_updated_at') }}

),

{# CTE affected_order_ids：五路变化取并集；这一步只决定“本次重算哪些订单”。 #}
affected_order_ids as (

    select order_id from changed_orders
    union
    select order_id from changed_transactions
    union
    select order_id from changed_refunds
    union
    select order_id from changed_fulfillments
    union
    select order_id from changed_fulfillment_events

),

{# CTE affected_orders：回读受影响订单的完整 Current 行；此处故意不再次施加 Execution Window。 #}
affected_orders as (

    -- 注意：这里不再加 Execution Window。
    -- affected_order_ids 已经确定了“本次要重算谁”，现在必须拿这些订单完整的 CURRENT 行。
    select
        o.order_id,
        o.store_id,
        o.order_created_at as order_time,
        o.order_processed_at as processed_at,
        o.cancelled_at,
        o.closed_at,
        o.financial_status,
        o.fulfillment_status,
        o.currency_code,
        o.original_total_amount,
        o.current_total_amount,
        o.current_total_discount_amount,
        o.total_refunded_amount,
        o.source_updated_at,
        o.extracted_at
    from {{ ref('int_shopify__orders_canonical') }} o
    inner join affected_order_ids affected
        on o.order_id = affected.order_id

),

{# CTE payment_milestones：把 Transaction Current Facts 聚合成订单级授权/收款里程碑；AUTHORIZATION ≠ PAID。 #}
payment_milestones as (

    select
        t.order_id,

        -- Authorization 表示授权成功，不等于资金已经完成 Capture / Sale。
        min(
            case
                when t.transaction_status = 'SUCCESS'
                 and t.transaction_kind in ('AUTHORIZATION', 'EMV_AUTHORIZATION')
                 and coalesce(t.is_test, false) = false
                then coalesce(t.transaction_processed_at, t.transaction_created_at)
            end
        ) as first_authorized_at,

        -- paid_at 采用第一次真正成功收款的 CAPTURE / SALE 业务时间。
        min(
            case
                when t.transaction_status = 'SUCCESS'
                 and t.transaction_kind in ('CAPTURE', 'SALE')
                 and coalesce(t.is_test, false) = false
                then coalesce(t.transaction_processed_at, t.transaction_created_at)
            end
        ) as first_paid_at,

        sum(
            case
                when t.transaction_status = 'SUCCESS'
                 and t.transaction_kind in ('CAPTURE', 'SALE')
                 and coalesce(t.is_test, false) = false
                then 1 else 0
            end
        ) as successful_collection_count,

        max(t.source_updated_at) as payment_source_updated_at,
        max(t.extracted_at) as payment_source_extracted_at

    from {{ ref('int_shopify__transactions_canonical') }} t
    inner join affected_order_ids affected
        on t.order_id = affected.order_id
    group by t.order_id

),

{# CTE refund_milestones：按 order_id 聚合退款首次发生时间与次数。 #}
refund_milestones as (

    select
        r.order_id,
        min(coalesce(r.processed_at, r.created_at)) as first_refund_at,
        count(*) as refund_count,
        max(r.source_updated_at) as refund_source_updated_at,
        max(r.extracted_at) as refund_source_extracted_at
    from {{ ref('int_shopify__refunds_canonical') }} r
    inner join affected_order_ids affected
        on r.order_id = affected.order_id
    group by r.order_id

),

{# CTE fulfillment_milestones：按 order_id 聚合 Fulfillment 自身显式时间与送达计数。 #}
fulfillment_milestones as (

    select
        f.order_id,
        min(f.fulfillment_created_at) as first_fulfillment_at,
        min(f.in_transit_at) as first_in_transit_at_from_fulfillment,
        min(f.delivered_at) as first_delivered_at_from_fulfillment,
        max(f.delivered_at) as latest_delivered_at,
        count(*) as fulfillment_count,
        sum(case when f.delivered_at is not null then 1 else 0 end) as delivered_fulfillment_count,
        max(f.source_updated_at) as fulfillment_source_updated_at,
        max(f.extracted_at) as fulfillment_source_extracted_at
    from {{ ref('int_shopify__fulfillments_canonical') }} f
    inner join affected_order_ids affected
        on f.order_id = affected.order_id
    group by f.order_id

),

{# CTE fulfillment_event_milestones：从事件流补充同义的 IN_TRANSIT / DELIVERED 业务时间。 #}
fulfillment_event_milestones as (

    select
        e.order_id,
        min(case when e.event_status = 'IN_TRANSIT' then e.event_time end) as first_in_transit_at_from_event,
        min(case when e.event_status = 'DELIVERED' then e.event_time end) as first_delivered_at_from_event,
        max(e.source_updated_at) as event_source_updated_at,
        max(e.extracted_at) as event_source_extracted_at
    from {{ ref('int_shopify__fulfillment_events_canonical') }} e
    inner join affected_order_ids affected
        on e.order_id = affected.order_id
    group by e.order_id

),

{# CTE joined：把各子 Grain 已聚合的里程碑安全 left join 回一订单一行，避免 Fanout。 #}
joined as (

    select
        o.*,
        p.first_authorized_at,
        p.first_paid_at,
        p.successful_collection_count,
        r.first_refund_at,
        r.refund_count,
        f.first_fulfillment_at,

        -- Fulfillment 字段和 FulfillmentEvent 都是源端显式业务时间。
        -- 两边都有值时取更早的同义里程碑；任何一边为空时使用另一边。
        case
            when f.first_in_transit_at_from_fulfillment is null
                then e.first_in_transit_at_from_event
            when e.first_in_transit_at_from_event is null
                then f.first_in_transit_at_from_fulfillment
            else least(
                f.first_in_transit_at_from_fulfillment,
                e.first_in_transit_at_from_event
            )
        end as first_in_transit_at,

        case
            when f.first_delivered_at_from_fulfillment is null
                then e.first_delivered_at_from_event
            when e.first_delivered_at_from_event is null
                then f.first_delivered_at_from_fulfillment
            else least(
                f.first_delivered_at_from_fulfillment,
                e.first_delivered_at_from_event
            )
        end as first_delivered_at,

        f.latest_delivered_at,
        coalesce(p.successful_collection_count, 0) as successful_collection_count_resolved,
        coalesce(r.refund_count, 0) as refund_count_resolved,
        coalesce(f.fulfillment_count, 0) as fulfillment_count_resolved,
        coalesce(f.delivered_fulfillment_count, 0) as delivered_fulfillment_count_resolved,

        greatest(
            o.source_updated_at,
            coalesce(p.payment_source_updated_at, o.source_updated_at),
            coalesce(r.refund_source_updated_at, o.source_updated_at),
            coalesce(f.fulfillment_source_updated_at, o.source_updated_at),
            coalesce(e.event_source_updated_at, o.source_updated_at)
        ) as lifecycle_source_updated_at,

        greatest(
            o.extracted_at,
            coalesce(p.payment_source_extracted_at, o.extracted_at),
            coalesce(r.refund_source_extracted_at, o.extracted_at),
            coalesce(f.fulfillment_source_extracted_at, o.extracted_at),
            coalesce(e.event_source_extracted_at, o.extracted_at)
        ) as lifecycle_source_extracted_at

    from affected_orders o
    left join payment_milestones p on o.order_id = p.order_id
    left join refund_milestones r on o.order_id = r.order_id
    left join fulfillment_milestones f on o.order_id = f.order_id
    left join fulfillment_event_milestones e on o.order_id = e.order_id

),

{# CTE modeled：形成最终 Accumulating Snapshot 输出、状态标志、Lead Time 与技术时钟。 #}
modeled as (

    select
        order_id,
        store_id,

        -- 业务生命周期时间。
        order_time,
        processed_at,
        first_authorized_at,
        first_paid_at,
        first_refund_at,
        first_fulfillment_at,
        first_in_transit_at,
        first_delivered_at,
        latest_delivered_at,
        cancelled_at,
        closed_at,

        -- 当前状态仍来自 Order Canonical Current State；
        -- 它和上面的历史里程碑时间承担不同职责。
        financial_status,
        fulfillment_status,
        currency_code,
        original_total_amount,
        current_total_amount,
        current_total_discount_amount,
        total_refunded_amount,

        successful_collection_count_resolved as successful_collection_count,
        refund_count_resolved as refund_count,
        fulfillment_count_resolved as fulfillment_count,
        delivered_fulfillment_count_resolved as delivered_fulfillment_count,

        case when first_paid_at is not null then 1 else 0 end as paid_flag,
        case when first_refund_at is not null then 1 else 0 end as refunded_flag,
        case when first_fulfillment_at is not null then 1 else 0 end as fulfillment_started_flag,
        case when first_in_transit_at is not null then 1 else 0 end as shipped_flag,
        case when first_delivered_at is not null then 1 else 0 end as delivered_flag,
        case when cancelled_at is not null then 1 else 0 end as cancelled_flag,
        case when closed_at is not null then 1 else 0 end as closed_flag,

        -- 累计快照常同时保存里程碑和可直接分析的 Lead Time。
        -- 只有时间顺序合理时才计算，避免把异常源数据转换成负时长。
        case
            when first_authorized_at >= order_time
            then (unix_timestamp(first_authorized_at) - unix_timestamp(order_time)) / 60.0
        end as time_to_authorize_minutes,

        case
            when first_paid_at >= order_time
            then (unix_timestamp(first_paid_at) - unix_timestamp(order_time)) / 60.0
        end as time_to_pay_minutes,

        case
            when first_fulfillment_at >= order_time
            then (unix_timestamp(first_fulfillment_at) - unix_timestamp(order_time)) / 60.0
        end as time_to_first_fulfillment_minutes,

        case
            when first_in_transit_at >= order_time
            then (unix_timestamp(first_in_transit_at) - unix_timestamp(order_time)) / 60.0
        end as time_to_ship_minutes,

        case
            when first_delivered_at >= order_time
            then (unix_timestamp(first_delivered_at) - unix_timestamp(order_time)) / 60.0
        end as time_to_first_delivery_minutes,

        case
            when closed_at >= order_time
            then (unix_timestamp(closed_at) - unix_timestamp(order_time)) / 60.0
        end as time_to_close_minutes,

        lifecycle_source_updated_at as source_updated_at,
        lifecycle_source_extracted_at as source_extracted_at

    from joined

)

select *
from modeled
