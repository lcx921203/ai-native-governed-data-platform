select
    fulfillment_id,
    order_id,
    fulfillment_name,
    upper(fulfillment_status) as fulfillment_status,
    upper(display_status) as display_status,
    fulfillment_created_at,
    last_source_updated_at as source_updated_at,
    in_transit_at,
    delivered_at,
    estimated_delivery_at,
    fulfillment_location_id,
    fulfillment_location_name,
    total_quantity,
    requires_shipping,
    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'fulfillments') }}
