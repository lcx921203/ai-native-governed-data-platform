select
    item_id,
    sku,
    item_name,
    brand,
    category,
    subcategory,
    price,
    status,
    source_updated_at
from {{ source('master_data', 'item_current') }}
