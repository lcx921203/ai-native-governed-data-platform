{#
  Staging / Order — Structured Source 到 dbt 的最薄标准化层。

  业务逻辑：保留 Structured Source 中每个 distinct Business Version，只做命名、类型和基础标准化。
  输入：source('shopify', 'orders')，其 Grain = order_id × distinct record_hash。
  输出：同 Grain 的 stg_shopify__orders；不会在这里 row_number() 取 latest。
  dbt API：source() 表示读取受治理 Source 声明，而不是直接写物理表名。
  工程边界：Current State 的 winner 选择属于 Intermediate；把 latest 提前到 Staging 会擦掉历史业务版本。
#}

select
    order_id,
    order_name,
    store_id,
    created_at as order_created_at,
    processed_at as order_processed_at,
    last_source_updated_at as source_updated_at,
    cancelled_at,
    closed_at,
    upper(financial_status) as financial_status,
    upper(fulfillment_status) as fulfillment_status,
    currency_code,
    original_total_amount,
    current_total_amount,
    current_total_discount_amount,
    total_refunded_amount,
    record_hash,
    first_observed_at,
    last_observed_at,
    first_source_updated_at,
    last_source_updated_at,
    extracted_at,
    batch_id
from {{ source('shopify', 'orders') }}
