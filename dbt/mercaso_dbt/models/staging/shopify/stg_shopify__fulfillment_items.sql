select
    fulfillment_line_item_id,
    fulfillment_id,
    order_id,
    line_item_id,
    fulfilled_quantity,
    last_source_updated_at as source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'fulfillment_items') }}
