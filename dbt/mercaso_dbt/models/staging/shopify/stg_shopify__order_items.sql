select
    line_item_id,
    order_id,
    item_id,
    variant_id,
    sku,
    trim(item_title) as item_title,

    quantity as ordered_quantity,
    current_quantity,
    refundable_quantity,
    unfulfilled_quantity,

    original_unit_price,
    original_total_amount as gross_sales_amount,

    -- 保留源字段语义；真正业务折扣使用 DiscountAllocation 汇总。
    source_line_discount_amount,

    currency_code,
    last_source_updated_at as source_updated_at,
    record_hash,
    extracted_at,
    batch_id
from {{ source('shopify', 'order_items') }}
