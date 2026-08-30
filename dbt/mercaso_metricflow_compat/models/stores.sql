{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.stores.
select *
from polaris.analytics.stores
