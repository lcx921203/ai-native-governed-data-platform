select
    item_id,
    sku,
    item_name,
    brand,
    category,
    subcategory
from {{ ref('stg_master__items') }}
