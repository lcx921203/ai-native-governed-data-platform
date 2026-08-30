select
    event_id,
    event_name,
    nullif(user_id, '') as user_id,
    nullif(session_id, '') as session_id,
    nullif(item_id, '') as item_id,
    nullif(store_id, '') as store_id,
    timestamp_millis(event_time_ms) as event_time,
    timestamp_millis(collector_received_at_ms) as collector_received_at,
    nullif(page_url, '') as page_url,
    nullif(device_type, '') as device_type,
    properties_json,
    raw_json
from {{ source('behavior', 'events') }}
