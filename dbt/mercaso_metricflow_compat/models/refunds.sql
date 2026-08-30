{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.refunds.
select *
from polaris.analytics.refunds
