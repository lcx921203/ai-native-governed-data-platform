-- Fixed BI consumer examples over the precomputed Serving Projection.
-- Metric formulas are intentionally absent here: MetricFlow remains the Metric Authority.

-- One business-day executive dashboard.
SELECT
  business_date,
  region,
  gross_sales,
  sales_before_reversal,
  net_sales,
  order_count,
  average_order_value
FROM iceberg.serving.bi_daily_executive
WHERE business_date = DATE '2026-08-20'
ORDER BY region;

-- Region trend. The dashboard reads the stable daily Grain rather than recalculating AOV or net sales.
SELECT
  business_date,
  region,
  gross_sales,
  net_sales,
  order_count,
  average_order_value
FROM iceberg.serving.bi_daily_executive
WHERE region = 'West'
  AND business_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-20'
ORDER BY business_date;
