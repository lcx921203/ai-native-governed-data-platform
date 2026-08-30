{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.order_items.
select *
from polaris.analytics.order_items
