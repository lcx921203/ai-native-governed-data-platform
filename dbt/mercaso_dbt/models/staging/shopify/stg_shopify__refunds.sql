select
    refund_id,
    order_id,
    created_at,
    processed_at,
    last_source_updated_at as source_updated_at,
    total_refunded_amount,
    currency_code,
    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'refunds') }}
