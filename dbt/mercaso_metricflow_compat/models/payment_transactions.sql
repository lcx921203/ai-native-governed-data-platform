{{ config(materialized='view') }}

-- Compatibility view only. Canonical business mart lives in polaris.analytics.payment_transactions.
select *
from polaris.analytics.payment_transactions
