{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.orders.
select *
from polaris.analytics.orders
