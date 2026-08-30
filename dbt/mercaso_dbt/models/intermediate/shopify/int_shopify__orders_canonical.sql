{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='order_id',
    file_format='iceberg',
    on_schema_change='fail',
    tags=['shopify_windowed', 'shopify_current_state']
) }}

{#
  Shopify Order Canonical Current State — one row per order_id.

  业务逻辑：Execution Window 只发现 changed order_id；真正选 Current Winner 时必须把“窗口内候选版本”与“该 key 现有 Current 行”放在同一个 candidate_pool 中比较。
  输入：stg_shopify__orders（仍含历史 Business Versions）+ 增量运行时的 {{ this }} 当前表。
  输出：Grain = 1 order_id = 1 Current Row。
  dbt API：is_incremental() 只在增量 MERGE 运行时为真；{{ this }} 指向当前模型已经存在的目标关系。
  排序语义：source_updated_at 优先，last_observed_at 作为稳定次级时钟；它们是版本选择时钟，不是订单业务发生时间。
  工程边界：只在 source_candidates 里施加 Execution Window；若对 candidate_pool 再过滤窗口，会丢掉当前完整上下文并可能选错 winner。
#}

{# CTE source_candidates：读取本执行窗口内的版本候选；这里只负责发现“这次有什么变化”。 #}
with source_candidates as (

    select
        order_id,
        order_name,
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
        current_total_discount_amount,
        total_refunded_amount,
        source_updated_at,
        record_hash,
        last_observed_at,
        extracted_at,
        batch_id
    from {{ ref('stg_shopify__orders') }}
    {% if shopify_window_is_configured() %}
    where {{ shopify_window_predicate('source_updated_at') }}
    {% endif %}

),

{# CTE changed_keys：把版本级变化收敛成受影响的业务键 order_id。 #}
changed_keys as (

    select distinct order_id
    from source_candidates

),

{# CTE candidate_pool：新候选 + 该 order_id 已有 Current Row；这是正确选 winner 所需的完整上下文。 #}
candidate_pool as (

    select *
    from source_candidates

    {% if is_incremental() %}
    union all
    select
        current.order_id,
        current.order_name,
        current.store_id,
        current.order_created_at,
        current.order_processed_at,
        current.cancelled_at,
        current.closed_at,
        current.financial_status,
        current.fulfillment_status,
        current.currency_code,
        current.original_total_amount,
        current.current_total_amount,
        current.current_total_discount_amount,
        current.total_refunded_amount,
        current.source_updated_at,
        current.record_hash,
        current.last_observed_at,
        current.extracted_at,
        current.batch_id
    from {{ this }} current
    inner join changed_keys changed
        on current.order_id = changed.order_id
    {% endif %}

),

{# CTE ranked：按 order_id 分区，用 row_number() 选 source clock 最新的 Current Winner。 #}
ranked as (

    select
        *,
        row_number() over (
            partition by order_id
            order by
                source_updated_at desc,
                last_observed_at desc
        ) as version_rank
    from candidate_pool

)

select
    order_id,
    order_name,
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
    current_total_discount_amount,
    total_refunded_amount,
    source_updated_at,
    record_hash,
    last_observed_at,
    extracted_at,
    batch_id
from ranked
where version_rank = 1
