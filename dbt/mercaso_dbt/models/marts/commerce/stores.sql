select
    store_id,
    store_name,
    region,
    state,
    country
from {{ ref('stg_master__stores') }}
