select
    store_id,
    store_name,
    region,
    state,
    country,
    status,
    source_updated_at
from {{ source('master_data', 'store_current') }}
