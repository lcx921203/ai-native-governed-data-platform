{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.refund_items.
select *
from polaris.analytics.refund_items
