{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.items.
select *
from polaris.analytics.items
