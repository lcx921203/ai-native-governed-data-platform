select
    refund_id,
    order_id,
    transaction_id,
    last_source_updated_at as source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'refund_transactions') }}
