select
    refund_line_item_id,
    refund_id,
    order_id,
    line_item_id,
    quantity,
    restocked,
    upper(restock_type) as restock_type,
    subtotal_amount,
    tax_amount,
    currency_code,
    last_source_updated_at as source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'refund_items') }}
