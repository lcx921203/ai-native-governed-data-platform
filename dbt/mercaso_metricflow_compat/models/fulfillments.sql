{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.fulfillments.
select *
from polaris.analytics.fulfillments
