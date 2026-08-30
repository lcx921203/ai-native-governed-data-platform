select
    order_id,
    line_item_id,
    discount_application_index,
    discount_application_type,
    allocation_method,
    target_selection,
    target_type,
    allocated_amount,
    currency_code,
    last_source_updated_at as source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'line_item_discount_allocations') }}
