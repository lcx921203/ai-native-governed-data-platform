{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.order_lifecycle_snapshot.
select *
from polaris.analytics.order_lifecycle_snapshot
