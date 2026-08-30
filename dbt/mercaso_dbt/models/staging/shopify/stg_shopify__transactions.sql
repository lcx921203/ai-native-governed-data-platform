select
    transaction_id,
    order_id,
    parent_transaction_id,
    upper(kind) as transaction_kind,
    upper(status) as transaction_status,
    gateway,
    created_at as transaction_created_at,
    processed_at as transaction_processed_at,
    amount as transaction_amount,
    currency_code,
    is_test,
    error_code,

    -- Structured Source 已将父 Order.updatedAt 的重复观测收敛到版本时钟。
    last_source_updated_at as source_updated_at,

    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'transactions') }}
