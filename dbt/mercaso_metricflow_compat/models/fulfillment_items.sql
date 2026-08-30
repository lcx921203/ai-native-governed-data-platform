{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.fulfillment_items.
select *
from polaris.analytics.fulfillment_items
