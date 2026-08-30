{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.fulfillment_events.
select *
from polaris.analytics.fulfillment_events
